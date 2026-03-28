export type DesktopProductError = Error & {
  code: string;
  detail: string;
  status?: number;
  userMessage: string;
};

export function createDesktopProductError({
  status,
  detail,
  userMessage = "系统请求失败，请稍后重试。",
}: {
  status?: number;
  detail?: string;
  userMessage?: string;
}): DesktopProductError {
  const error = new Error(userMessage) as DesktopProductError;
  error.code = status ? `HTTP_${status}` : "DESKTOP_REQUEST_FAILED";
  error.detail = String(detail || "").trim();
  error.status = status;
  error.userMessage = userMessage;
  return error;
}
