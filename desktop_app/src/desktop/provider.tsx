import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useMemo, useState } from "react";
import { BackendConfig } from "./contracts";
import { createDesktopCommands } from "./commands";
import { createDesktopHttpClient } from "./http";

type DesktopRuntime = {
  config: BackendConfig;
  commands: ReturnType<typeof createDesktopCommands>;
};

const DesktopRuntimeContext = createContext<DesktopRuntime | null>(null);

type DesktopProviderProps = {
  config: BackendConfig;
  children: React.ReactNode;
};

export function DesktopProvider({ config, children }: DesktopProviderProps) {
  const [queryClient] = useState(() => new QueryClient());
  const runtime = useMemo(() => {
    const client = createDesktopHttpClient({
      baseUrl: config.backendUrl,
      apiToken: config.apiToken,
    });
    return {
      config,
      commands: createDesktopCommands({ client }),
    };
  }, [config]);

  return (
    <QueryClientProvider client={queryClient}>
      <DesktopRuntimeContext.Provider value={runtime}>
        {children}
      </DesktopRuntimeContext.Provider>
    </QueryClientProvider>
  );
}

export function useDesktopRuntime() {
  const runtime = useContext(DesktopRuntimeContext);
  if (!runtime) {
    throw new Error("DesktopProvider is missing");
  }
  return runtime;
}
