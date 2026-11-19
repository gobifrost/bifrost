import { create } from "zustand";
import { persist } from "zustand/middleware";

interface PresentationState {
	enabled: boolean;
	toggle: () => void;
	setEnabled: (enabled: boolean) => void;
	enableFromSearch: (search: string) => void;
}

export const usePresentationStore = create<PresentationState>()(
	persist(
		(set) => ({
			enabled: false,
			toggle: () => set((state) => ({ enabled: !state.enabled })),
			setEnabled: (enabled) => set({ enabled }),
			enableFromSearch: (search) => {
				if (new URLSearchParams(search).get("presentation") === "1") {
					set({ enabled: true });
				}
			},
		}),
		{ name: "bifrost-presentation-mode" },
	),
);
