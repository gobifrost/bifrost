import { format } from "date-fns";
import {
	LineChart,
	Line,
	XAxis,
	YAxis,
	CartesianGrid,
	Tooltip,
	ResponsiveContainer,
	Legend,
} from "recharts";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { UsageTrend } from "@/services/usage";
import {
	formatChartDateLabel,
	formatCurrency,
	formatNumber,
} from "./formatters";

export interface UsageChartsProps {
	trends: UsageTrend[] | undefined;
	isLoading: boolean;
}

export function UsageCharts({ trends, isLoading }: UsageChartsProps) {
	return (
		<Card>
			<CardHeader>
				<CardTitle>Cost Over Time</CardTitle>
				<CardDescription>
					AI cost trends during the selected period
				</CardDescription>
			</CardHeader>
			<CardContent>
				{isLoading ? (
					<Skeleton className="h-[300px] w-full" />
				) : trends && trends.length > 0 ? (
					<ResponsiveContainer width="100%" height={300}>
						<LineChart data={trends}>
							<CartesianGrid
								strokeDasharray="3 3"
								className="stroke-muted"
							/>
							<XAxis
								dataKey="date"
								className="text-xs"
								tick={{ fontSize: 12 }}
								tickFormatter={(value) =>
									format(new Date(value), "MMM dd")
								}
							/>
							<YAxis
								className="text-xs"
								tick={{ fontSize: 12 }}
								tickFormatter={(value) => `$${value}`}
								label={{
									value: "Cost (USD)",
									angle: -90,
									position: "insideLeft",
									fontSize: 12,
								}}
							/>
							<Tooltip
								contentStyle={{
									backgroundColor: "hsl(var(--card))",
									border: "1px solid hsl(var(--border))",
									borderRadius: "6px",
								}}
								formatter={(value, name) => {
									if (name === "ai_cost")
										return [
											formatCurrency(value as string | number),
											"AI Cost",
										];
									return [
										formatNumber(value as number),
										name as string,
									];
								}}
								labelFormatter={formatChartDateLabel}
							/>
							<Legend
								formatter={(value) => {
									if (value === "ai_cost") return "AI Cost";
									if (value === "input_tokens")
										return "Input Tokens";
									if (value === "output_tokens")
										return "Output Tokens";
									return value;
								}}
							/>
							<Line
								type="monotone"
								dataKey="ai_cost"
								stroke="hsl(var(--chart-1, 220 70% 50%))"
								strokeWidth={2}
								dot={{ r: 3 }}
								activeDot={{ r: 5 }}
							/>
						</LineChart>
					</ResponsiveContainer>
				) : (
					<div className="flex items-center justify-center h-[300px] text-muted-foreground">
						No trend data available for this period
					</div>
				)}
			</CardContent>
		</Card>
	);
}
