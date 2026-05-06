import { create } from "zustand";

/**
 * Notification Center store using Zustand
 *
 * Handles two types of notifications:
 * 1. Progress notifications - Long-running operations from WebSocket (GitHub setup, file uploads)
 * 2. One-off notifications - Dismissable alerts (success, error, warning, info)
 *
 * Progress notifications come from WebSocket via notification:{user_id} channel.
 * They have status (pending/running/completed/failed) and optional percent (0-100).
 */

// Categories matching backend NotificationCategory enum
export type NotificationCategory =
	| "github_setup"
	| "github_sync"
	| "file_upload"
	| "package_install"
	| "embedding_reindex"
	| "system";

// Status matching backend NotificationStatus enum
export type NotificationStatus =
	| "pending"
	| "running"
	| "awaiting_action" // Waiting for user action (no spinner, shows action button)
	| "completed"
	| "failed"
	| "cancelled";

// Main notification type (matches backend NotificationPublic)
export interface Notification {
	id: string;
	category: NotificationCategory;
	title: string;
	description: string | null;
	status: NotificationStatus;
	percent: number | null; // null = indeterminate spinner, 0-100 = progress bar
	error: string | null;
	result: Record<string, unknown> | null;
	metadata: Record<string, unknown> | null;
	createdAt: string;
	updatedAt: string;
	userId: string;
}

// One-off notification type for local alerts
export type AlertStatus = "success" | "error" | "warning" | "info";

export interface OneOffNotification {
	id: string;
	title: string;
	body: string;
	status: AlertStatus;
	link?: string; // Optional navigation target
	createdAt: string;
}

interface NotificationState {
	// WebSocket-driven notifications (progress)
	notifications: Notification[];

	// Local one-off alerts
	alerts: OneOffNotification[];

	// Actions for WebSocket notifications
	setNotification: (notification: Notification) => void;
	updateNotification: (id: string, updates: Partial<Notification>) => void;
	removeNotification: (id: string) => void;
	clearNotifications: () => void;

	// Actions for one-off alerts
	addAlert: (alert: Omit<OneOffNotification, "id" | "createdAt">) => void;
	removeAlert: (id: string) => void;
	clearAlerts: () => void;
}

export const useNotificationStore = create<NotificationState>()((set) => ({
	notifications: [],
	alerts: [],

	// WebSocket notification handlers
	setNotification: (notification) => {
		set((state) => {
			const existing = state.notifications.findIndex(
				(n) => n.id === notification.id,
			);
			if (existing >= 0) {
				// Update existing
				const updated = [...state.notifications];
				updated[existing] = notification;
				return { notifications: updated };
			}
			// Add new (newest first)
			return { notifications: [notification, ...state.notifications] };
		});
	},

	updateNotification: (id, updates) => {
		set((state) => ({
			notifications: state.notifications.map((n) =>
				n.id === id ? { ...n, ...updates } : n,
			),
		}));
	},

	removeNotification: (id) => {
		set((state) => ({
			notifications: state.notifications.filter((n) => n.id !== id),
		}));
	},

	clearNotifications: () => {
		set({ notifications: [] });
	},

	// One-off alert handlers
	addAlert: (alert) => {
		const id = crypto.randomUUID();
		const newAlert: OneOffNotification = {
			...alert,
			id,
			createdAt: new Date().toISOString(),
		};
		set((state) => ({
			alerts: [newAlert, ...state.alerts],
		}));
	},

	removeAlert: (id) => {
		set((state) => ({
			alerts: state.alerts.filter((a) => a.id !== id),
		}));
	},

	clearAlerts: () => {
		set({ alerts: [] });
	},
}));

// Helper to check if a notification is active (still in progress)
export const isActiveNotification = (notification: Notification): boolean => {
	return (
		notification.status === "pending" || notification.status === "running"
	);
};

// Helper to check if notification is complete (terminal state)
export const isCompleteNotification = (notification: Notification): boolean => {
	return (
		notification.status === "completed" ||
		notification.status === "failed" ||
		notification.status === "cancelled"
	);
};

// Helper to check if notification is awaiting user action
export const isAwaitingActionNotification = (
	notification: Notification,
): boolean => {
	return notification.status === "awaiting_action";
};

// Helper to get counts for badges
export const getNotificationCounts = (notifications: Notification[]) => {
	return {
		active: notifications.filter(isActiveNotification).length,
		awaitingAction: notifications.filter(isAwaitingActionNotification)
			.length,
		completed: notifications.filter((n) => n.status === "completed").length,
		failed: notifications.filter((n) => n.status === "failed").length,
		total: notifications.length,
	};
};

export const getAlertCounts = (alerts: OneOffNotification[]) => {
	return {
		error: alerts.filter((a) => a.status === "error").length,
		warning: alerts.filter((a) => a.status === "warning").length,
		info: alerts.filter((a) => a.status === "info").length,
		success: alerts.filter((a) => a.status === "success").length,
		total: alerts.length,
	};
};
