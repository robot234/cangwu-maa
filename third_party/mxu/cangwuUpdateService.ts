import { create } from 'zustand';
import { invoke } from '@tauri-apps/api/core';
import { useEffect } from 'react';
import { useAppStore, flushConfig } from '@/stores/appStore';
import { apiPost } from '@/utils/backendApi';
import { saveConfig } from '@/services/configService';
import i18n from '@/i18n';
import { isTauri } from '@/utils/paths';

export interface UpdateState {
  phase: string;
  message: string;
  current_version: string;
  auto_update: boolean;
  automatic?: boolean;
  target_version?: string;
  latest_version?: string;
  downloaded?: number;
  total?: number;
  mode?: string;
}
type Action = 'status' | 'configure' | 'check' | 'prepare' | 'sync' | 'install' | 'cancel';
type Reply = Partial<UpdateState> & { deferred?: boolean; restarting?: boolean };
async function command(action: Action, enabled?: boolean, automatic = false): Promise<Reply> {
  return isTauri()
    ? invoke('cangwu_update', { action, enabled: enabled ?? null, automatic })
    : apiPost('/cangwu/update', { action, enabled, automatic });
}

export const useCangwuUpdate = create<{
  status: UpdateState | null;
  error: string;
  requesting: boolean;
  perform: (action: Action, enabled?: boolean, automatic?: boolean) => Promise<Reply>;
}>((set) => ({
  status: null, error: '', requesting: false,
  perform: async (action, enabled, automatic = false) => {
    if (action !== 'status') set({ requesting: true, error: '' });
    try {
      const reply = await command(action, enabled, automatic);
      if (reply.phase) set({ status: reply as UpdateState });
      return reply;
    } catch (error) {
      if (action !== 'status') set({ error: String(error) });
      throw error;
    } finally {
      if (action !== 'status') set({ requesting: false });
    }
  },
}));

// One manager in the desktop app; browser clients only display/control settings.
export function useCangwuUpdateManager() {
  const name = useAppStore((s) => s.projectInterface?.name);
  const ready = useAppStore((s) => s.configPersistenceReady);
  useEffect(() => {
    if (name !== 'CangwuMaa' || !ready) return;
    let stopped = false;
    let polling = false;
    let lastAuto = 0;
    let installFailed = false;
    const tick = async () => {
      if (polling || stopped) return;
      polling = true;
      try {
        const store = useCangwuUpdate.getState();
        const status = await store.perform('status');
        if (stopped || !isTauri() || store.requesting) return;
        const app = useAppStore.getState();
        const idle = !app.instances.some((i) => i.isRunning)
          && !Object.values(app.instanceConnectionStatus).includes('Connecting');
        if (status.phase !== 'ready') installFailed = false;
        if (status.phase === 'ready' && idle && !installFailed && (!status.automatic || status.auto_update)) {
          const config = flushConfig();
          if (!config) return;
          try {
            // Use the native persistence path; tauri:// does not host /api.
            if (!await saveConfig(app.dataPath, config, app.projectInterface?.name)) {
              throw new Error(i18n.t('cangwuUpdate.saveFailed'));
            }
            await store.perform('install');
          } catch (error) {
            installFailed = true;
            useCangwuUpdate.setState({ error: String(error) });
          }
        } else if (status.auto_update && Date.now() - lastAuto > 6 * 3600 * 1000
          && !['checking', 'downloading', 'ready', 'waiting_exit', 'installing'].includes(status.phase || '')) {
          lastAuto = Date.now();
          await store.perform('prepare', undefined, true);
        }
      } catch { /* A failed poll never changes the saved update preference. */ }
      finally { polling = false; }
    };
    const first = window.setTimeout(tick, 5000);
    const timer = window.setInterval(tick, 2500);
    return () => { stopped = true; clearTimeout(first); clearInterval(timer); };
  }, [name, ready]);
}
