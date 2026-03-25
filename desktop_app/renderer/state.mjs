export const DEFAULT_RECORDS_PAGE_SIZE = 50;

export function createDesktopState() {
  return {
    backendUrl: "",
    backendApiToken: "",
    records: {
      page: 1,
      pageSize: DEFAULT_RECORDS_PAGE_SIZE,
      pageCount: 0,
      totalCount: 0,
    },
  };
}
