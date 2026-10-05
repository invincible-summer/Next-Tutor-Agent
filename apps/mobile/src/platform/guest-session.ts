/**
 * 游客会话：token 只存内存。
 * App 被终止即视为游客会话结束；登录时必须显式丢弃游客状态。
 */
let guestToken: string | null = null;

export function getGuestToken(): string | null {
  return guestToken;
}

export function setGuestToken(token: string | null): void {
  guestToken = token;
}

export function clearGuestSession(): void {
  guestToken = null;
}
