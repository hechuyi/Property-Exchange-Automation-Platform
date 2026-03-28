import { useEffect } from "react";
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DesktopProvider, useDesktopRuntime } from "./provider";

function OverviewProbe() {
  const runtime = useDesktopRuntime();

  useEffect(() => {
    void runtime.commands.getOverview();
  }, [runtime]);

  return null;
}

describe("DesktopProvider", () => {
  it("maps backendUrl into the http client baseUrl", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <DesktopProvider config={{ backendUrl: "http://127.0.0.1:42679", apiToken: "token" }}>
        <OverviewProbe />
      </DesktopProvider>,
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "http://127.0.0.1:42679/api/overview",
        expect.objectContaining({
          method: "GET",
        }),
      );
    });
  });
});
