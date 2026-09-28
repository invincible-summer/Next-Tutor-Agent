/* 应用级共享 audio focus（plan.md §5.4，阶段 G01）。
 *
 * 不同 run、同一浏览器只允许一个发声源：旧电话语音、课堂播放器、试听
 * 都必须先向 focus 申请；被抢占者立即暂停并释放。纯模块单例，不持引用
 * 计数——最后持有者卸载时释放。
 */
type FocusHolder = { id: string; pause: () => void };

let current: FocusHolder | null = null;

export function acquireAudioFocus(
  id: string,
  pause: () => void,
): () => boolean {
  if (current && current.id !== id) {
    current.pause();
  }
  current = { id, pause };
  return () => current !== null && current.id === id;
}

export function releaseAudioFocus(id: string): void {
  if (current && current.id === id) {
    current = null;
  }
}

export function audioFocusOwner(): string | null {
  return current ? current.id : null;
}
