/**
 * useNotifications Hook
 *
 * Provides notification management with automatic WebSocket subscription.
 * Fetches initial notifications on mount and receives real-time updates.
 */

import { useEffect, useCallback } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useAuth } from "@/contexts/AuthContext";
import { webSocketService } from "@/services/websocket";
import {
	useNotificationStore,
	isActiveNotification,
	isCompleteNotification,
} from "@/stores/notificationStore";
import {
	getNotifications,
	dismissNotification as dismissNotificationApi,
} from "@/services/notifications";

export function useNotifications() {
	const { user, isAuthenticated } = useAuth();

	// Get store state
	const notifications = useNotificationStore((state) => state.notifications);
	const setNotification = useNotificationStore(
		(state) => state.setNotification,
	);
	const removeNotification = useNotificationStore(
		(state) => state.removeNotification,
	);
	const clearNotifications = useNotificationStore(
		(state) => state.clearNotifications,
	);

	// Fetch initial notifications
	const { isLoading, error } = useQuery({
		queryKey: ["notifications"],
		queryFn: async () => {
			const data = await getNotifications();
			// Populate store with fetched notifications
			data.forEach((n) => setNotification(n));
			return data;
		},
		enabled: isAuthenticated,
		staleTime: 30000, // 30 seconds
		refetchOnWindowFocus: false,
	});

	// Dismiss mutation
	const dismissMutation = useMutation({
		mutationFn: dismissNotificationApi,
		onSuccess: (_, notificationId) => {
			removeNotification(notificationId);
		},
	});

	// Subscribe to notification WebSocket channel
	useEffect(() => {
		if (!isAuthenticated || !user?.id) return;

		// Connect to notification channel
		const notificationChannel = `notification:${user.id}`;
		webSocketService.connect([notificationChannel]);

		// Also subscribe to admin notifications if user is superuser
		if (user.isSuperuser) {
			webSocketService.subscribe("notification:admins");
		}

		return () => {
			// Unsubscribe on cleanup
			webSocketService.unsubscribe(notificationChannel);
			if (user.isSuperuser) {
				webSocketService.unsubscribe("notification:admins");
			}
		};
	}, [isAuthenticated, user?.id, user?.isSuperuser]);

	// Dismiss handler
	const dismiss = useCallback(
		async (notificationId: string) => {
			// Optimistic update
			removeNotification(notificationId);
			// Call API (will be no-op if notification already dismissed)
			try {
				await dismissMutation.mutateAsync(notificationId);
			} catch {
				// Ignore errors - notification may have expired
			}
		},
		[dismissMutation, removeNotification],
	);

	// Split notifications by type
	const activeNotifications = notifications.filter(isActiveNotification);
	const completedNotifications = notifications.filter(isCompleteNotification);

	return {
		notifications,
		activeNotifications,
		completedNotifications,
		isLoading,
		error,
		dismiss,
		clearAll: clearNotifications,
	};
}
