import { create } from "zustand"

type InputMode = "files" | "url"

interface UploadState {
  inputMode: InputMode
  files: File[] | null
  packageName: string | null
  sourceUrl: string | null
  setInputMode: (mode: InputMode) => void
  setUpload: (files: File[] | null, packageName: string | null) => void
  setSourceUrl: (url: string | null) => void
  clearUpload: () => void
}

export const useUploadStore = create<UploadState>((set) => ({
  inputMode: "files",
  files: null,
  packageName: null,
  sourceUrl: null,
  setInputMode: (inputMode) => set({ inputMode }),
  setUpload: (files, packageName) => set({ files, packageName, inputMode: "files" }),
  setSourceUrl: (sourceUrl) => set({ sourceUrl }),
  clearUpload: () => set({ files: null, packageName: null, sourceUrl: null }),
}))
