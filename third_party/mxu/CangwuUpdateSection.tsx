import { useTranslation } from 'react-i18next';
import { Download, RefreshCw, Loader2 } from 'lucide-react';
import { SwitchButton } from '@/components/FormControls';
import { useCangwuUpdate } from '@/services/cangwuUpdateService';

export function CangwuUpdateSection() {
  const { t } = useTranslation();
  const { status, error, requesting, perform } = useCangwuUpdate();
  const busy = requesting || ['checking', 'downloading', 'waiting_exit', 'installing'].includes(status?.phase || '');
  const progress = status?.total ? Math.min(100, Math.round(100 * (status.downloaded || 0) / status.total)) : null;
  const button = 'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm bg-bg-tertiary hover:bg-bg-hover border border-border disabled:opacity-50 disabled:cursor-not-allowed transition-colors';
  const act = (action: 'check' | 'prepare' | 'sync' | 'cancel', enabled?: boolean) => {
    void perform(action, enabled).catch(() => {});
  };
  return (
    <section id="section-update" className="space-y-4">
      <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Download className="w-5 h-5 text-accent" />{t('cangwuUpdate.title')}
      </h2>
      <div className="rounded-xl border border-border bg-bg-secondary p-4 space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm font-medium text-text-primary">{t('cangwuUpdate.auto')}</div>
            <p className="text-xs text-text-secondary mt-1">{t('cangwuUpdate.autoHint')}</p>
          </div>
          <div role="group" aria-label={t('cangwuUpdate.auto')}>
            <SwitchButton value={status?.auto_update ?? false} disabled={!status || requesting}
              onChange={(enabled) => { void perform('configure', enabled).catch(() => {}); }} />
          </div>
        </div>
        <p className="text-xs text-text-secondary">{t('cangwuUpdate.version', { version: status?.current_version || '—' })}</p>
        <div className="flex flex-wrap gap-2">
          <button className={button} disabled={busy || !status} onClick={() => act('check')}>
            <RefreshCw className="w-4 h-4" />{t('cangwuUpdate.check')}
          </button>
          {(status?.phase === 'available' || status?.phase === 'ready') && (
            <button className={button} disabled={busy} onClick={() => act('prepare')}>
              <Download className="w-4 h-4" />{t('cangwuUpdate.install')}
            </button>
          )}
          <button className={button} disabled={busy || !status} onClick={() => act('sync')}>
            <RefreshCw className="w-4 h-4" />{t('cangwuUpdate.sync')}
          </button>
          {['checking', 'downloading', 'ready'].includes(status?.phase || '') && (
            <button className={button} disabled={requesting} onClick={() => act('cancel')}>{t('cangwuUpdate.cancel')}</button>
          )}
        </div>
        <p className="text-xs text-text-secondary">{t('cangwuUpdate.syncHint')}</p>
        <div className="text-sm text-text-secondary flex items-center gap-2" role="status" aria-live="polite">
          {busy && <Loader2 className="w-4 h-4 animate-spin flex-shrink-0" />}
          {status?.message || t('cangwuUpdate.loading')}{progress !== null && ` ${progress}%`}
        </div>
        {status?.phase === 'ready' && <p className="text-xs text-text-secondary">{t('cangwuUpdate.waitIdle')}</p>}
        {error && <p className="text-sm text-red-500" role="alert">{error}</p>}
      </div>
    </section>
  );
}
