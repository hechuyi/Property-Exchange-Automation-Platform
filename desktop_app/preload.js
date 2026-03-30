const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("peapDesktop", {
  getBackendUrl: () => ipcRenderer.invoke("peap:get-backend-url"),
  getBackendConfig: () => ipcRenderer.invoke("peap:get-backend-config"),
  openPath: (targetPath) => ipcRenderer.invoke("peap:open-path", targetPath),
  showItemInFolder: (targetPath) => ipcRenderer.invoke("peap:show-item-in-folder", targetPath),
  pickDirectory: (defaultPath) => ipcRenderer.invoke("peap:pick-directory", defaultPath),
  pickFile: (defaultPath) => ipcRenderer.invoke("peap:pick-file", defaultPath),
  restartBackend: () => ipcRenderer.invoke("peap:restart-backend"),
});
