import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Loader2, Sparkles, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '@/stores/appStore';
import {
  markdownToHtml,
  markdownToHtmlWithLocalImages,
  resolveContent,
  resolveI18nText,
} from '@/services/contentResolver';
import { getInterfaceLangKey } from '@/i18n';
import { loggers } from '@/utils/logger';
import { ModalPortal, useModalFocusTrap } from '@/components/ui/Modal';

type WelcomeContentState =
  | { status: 'loading'; html: ''; hash: '' }
  | { status: 'empty'; html: ''; hash: '' }
  | { status: 'ready'; html: string; hash: string };

interface WelcomeDialogViewProps {
  html: string;
  isLoading?: boolean;
  onClose: (suppressUntilUpdate: boolean) => void;
  title: string;
  initialSuppressUntilUpdate?: boolean;
}

interface ManualWelcomeDialogProps {
  onClose: () => void;
}

/** 计算字符串的简单 hash，用于判断 welcome 内容是否变化。 */
function simpleHash(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash = hash & hash;
  }
  return hash.toString(36);
}

/**
 * 解析当前语言下的 welcome 内容。
 * URL、ETag 缓存、本地文件和 Markdown 渲染统一从这里进入，展示策略不参与解析。
 */
function useWelcomeContent() {
  const projectInterface = useAppStore((state) => state.projectInterface);
  const interfaceTranslations = useAppStore((state) => state.interfaceTranslations);
  const basePath = useAppStore((state) => state.basePath);
  const language = useAppStore((state) => state.language);

  const welcome = projectInterface?.welcome;
  const welcomeVersion = `${projectInterface?.name ?? ''}:${projectInterface?.version ?? ''}`;
  const langKey = getInterfaceLangKey(language);
  const translations = interfaceTranslations[langKey];
  const title =
    resolveI18nText(projectInterface?.label, translations) || projectInterface?.name || 'Welcome';

  const [content, setContent] = useState<WelcomeContentState>(() =>
    welcome ? { status: 'loading', html: '', hash: '' } : { status: 'empty', html: '', hash: '' },
  );

  useEffect(() => {
    let cancelled = false;

    if (!welcome) {
      setContent({ status: 'empty', html: '', hash: '' });
      return;
    }

    setContent({ status: 'loading', html: '', hash: '' });

    const loadWelcome = async () => {
      const resolvedContent = await resolveContent(welcome, { translations, basePath });
      if (cancelled) return;

      if (!resolvedContent) {
        setContent({ status: 'empty', html: '', hash: '' });
        return;
      }

      let html: string;
      try {
        html = await markdownToHtmlWithLocalImages(resolvedContent, basePath);
      } catch (err) {
        if (cancelled) return;
        loggers.ui.warn('Welcome markdown 转 HTML 失败，降级为纯 markdown 渲染:', err);
        html = markdownToHtml(resolvedContent);
      }

      if (cancelled) return;
      setContent({
        status: 'ready',
        html,
        hash: simpleHash(`${welcomeVersion}\n${resolvedContent}`),
      });
    };

    void loadWelcome();

    return () => {
      cancelled = true;
    };
  }, [welcome, welcomeVersion, langKey, basePath, translations]);

  return {
    ...content,
    hasWelcome: Boolean(welcome),
    title,
  };
}

/** 只负责展示 welcome，不包含自动展示、hash 判断或持久化策略。 */
function WelcomeDialogView({
  html,
  isLoading = false,
  onClose: dismiss,
  title,
  initialSuppressUntilUpdate = false,
}: WelcomeDialogViewProps) {
  const { t } = useTranslation();
  const [suppressUntilUpdate, setSuppressUntilUpdate] = useState(initialSuppressUntilUpdate);
  useEffect(() => setSuppressUntilUpdate(initialSuppressUntilUpdate), [initialSuppressUntilUpdate]);
  const onClose = useCallback(() => dismiss(suppressUntilUpdate), [dismiss, suppressUntilUpdate]);
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useModalFocusTrap({
    containerRef: panelRef,
    initialFocusRef: closeButtonRef,
    onEscape: onClose,
  });

  return (
    <ModalPortal>
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />

        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          className="relative bg-bg-secondary rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[80vh] flex flex-col animate-in fade-in zoom-in-95 duration-200"
        >
          <div className="flex items-center justify-between px-6 py-4 border-b border-border">
            <div className="flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-accent" />
              <h2 id={titleId} className="text-lg font-semibold text-text-primary">
                {title}
              </h2>
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              aria-label={t('common.close')}
              className="p-2 rounded-lg hover:bg-bg-hover transition-colors"
            >
              <X className="w-5 h-5 text-text-secondary" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto px-6 py-4">
            {isLoading ? (
              <div className="min-h-32 flex items-center justify-center gap-2 text-text-secondary">
                <Loader2 className="w-5 h-5 animate-spin text-accent" />
                <span className="text-sm">{t('common.loading')}</span>
              </div>
            ) : (
              <div
                className="prose prose-sm max-w-none text-text-secondary"
                dangerouslySetInnerHTML={{ __html: html }}
              />
            )}
          </div>

          {!isLoading && (
            <div className="px-6 py-4 border-t border-border flex flex-wrap items-center justify-between gap-4">
              <label className="inline-flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
                <input
                  type="checkbox"
                  checked={suppressUntilUpdate}
                  onChange={(event) => setSuppressUntilUpdate(event.target.checked)}
                  className="h-4 w-4 accent-accent cursor-pointer"
                />
                <span>{t('welcome.suppressUntilUpdate')}</span>
              </label>
              <button
                type="button"
                onClick={onClose}
                className="shrink-0 px-4 py-2.5 bg-accent hover:bg-accent-hover text-white rounded-lg font-medium transition-colors"
              >
                {t('welcome.dismiss')}
              </button>
            </div>
          )}
        </div>
      </div>
    </ModalPortal>
  );
}

/** 启动时使用：勾选后按版本和公告内容记住；未勾选时只关闭当前会话。 */
export function AutoWelcomeDialog() {
  const content = useWelcomeContent();
  const [dismissedHash, setDismissedHash] = useState('');
  const welcomeShownHash = useAppStore((state) => state.welcomeShownHash);
  const setWelcomeShownHash = useAppStore((state) => state.setWelcomeShownHash);

  useEffect(() => {
    if (content.status === 'ready' && welcomeShownHash === content.hash)
      setDismissedHash(content.hash);
  }, [content.status, content.hash, welcomeShownHash]);

  const handleClose = useCallback(
    (suppressUntilUpdate: boolean) => {
      if (content.status === 'ready') {
        setWelcomeShownHash(suppressUntilUpdate ? content.hash : '');
        setDismissedHash(content.hash);
      }
    },
    [content, setWelcomeShownHash],
  );

  if (!content.hasWelcome) return null;

  if (content.status === 'loading') {
    // 让 OnboardingOverlay 感知 welcome 的展示决策尚未完成，避免教程抢先弹出。
    return <div className="fixed inset-0 z-50 pointer-events-none" aria-hidden />;
  }

  if (
    content.status !== 'ready' ||
    welcomeShownHash === content.hash ||
    dismissedHash === content.hash
  )
    return null;

  return <WelcomeDialogView html={content.html} title={content.title} onClose={handleClose} />;
}

/** 用户主动查看时始终展示当前公告，并允许调整下次启动时的提醒选择。 */
export function ManualWelcomeDialog({ onClose }: ManualWelcomeDialogProps) {
  const content = useWelcomeContent();
  const welcomeShownHash = useAppStore((state) => state.welcomeShownHash);
  const setWelcomeShownHash = useAppStore((state) => state.setWelcomeShownHash);
  const handleClose = useCallback(
    (suppressUntilUpdate: boolean) => {
      if (content.status === 'ready') setWelcomeShownHash(suppressUntilUpdate ? content.hash : '');
      onClose();
    },
    [content, onClose, setWelcomeShownHash],
  );

  useEffect(() => {
    if (content.hasWelcome && content.status === 'empty') onClose();
  }, [content.hasWelcome, content.status, onClose]);

  if (!content.hasWelcome || content.status === 'empty') return null;

  return (
    <WelcomeDialogView
      html={content.html}
      isLoading={content.status === 'loading'}
      title={content.title}
      onClose={handleClose}
      initialSuppressUntilUpdate={content.status === 'ready' && welcomeShownHash === content.hash}
    />
  );
}
