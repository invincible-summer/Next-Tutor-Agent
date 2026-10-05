import { Alert } from "react-native";
import { ApiError } from "@next-tutor/api-client";
export function confirm(
  title: string,
  message: string,
  yes: string,
  no = "取消 / Cancel",
) {
  return new Promise<boolean>((resolve) =>
    Alert.alert(
      title,
      message,
      [
        { text: no, style: "cancel", onPress: () => resolve(false) },
        { text: yes, onPress: () => resolve(true) },
      ],
      { cancelable: true, onDismiss: () => resolve(false) },
    ),
  );
}
export function errorMessage(
  error: unknown,
  c: (zh: string, en: string) => string,
) {
  if (error instanceof ApiError) {
    if (error.status === 401)
      return c(
        "登录已过期，请重新登录",
        "Your session expired. Please sign in again.",
      );
    if (error.status === 403)
      return c(
        "当前账号没有此操作权限",
        "Your account cannot perform this action.",
      );
    if (error.status === 404)
      return c(
        "内容已删除或当前不可访问",
        "This content was removed or is unavailable.",
      );
    if (error.status === 409)
      return c(
        "内容已发生变化，请刷新后再试",
        "This content changed. Refresh and try again.",
      );
    if (error.status === 413)
      return c("内容超出大小限制", "This content exceeds the size limit.");
    if (error.status === 429)
      return c(
        "操作较频繁，请稍后再试",
        "Too many requests. Please try again later.",
      );
  }
  return c(
    "暂时无法完成，请检查网络后重试",
    "Unable to complete this action. Check your connection and retry.",
  );
}
