// client/src/pages/diagnostics/components/ContainerTable.tsx
import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight, Cloud, Server, Boxes } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { ForkTable } from "./ForkTable";
import type { ExecutionRowData } from "./ExecutionRow";
import type { ProcessInfo, PoolDetail, PoolSummary } from "@/services/workers";
import { CONTAINER_COLORS } from "./MemoryChart";
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip";

type PoolData = PoolSummary | PoolDetail;
type RuntimeKind = "AKS" | "VM" | "ACA" | "Unknown";

function formatUptime(seconds: number): string {
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        return m > 0 ? `${h}h ${m}m` : `${h}h`;
    }
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

function formatBytes(bytes: number): string {
    if (bytes < 0) return "N/A";
    const gb = bytes / (1024 * 1024 * 1024);
    if (gb >= 1) return `${gb.toFixed(1)} GB`;
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(0)} MB`;
}

function getPoolCounts(pool: PoolData) {
    if ("processes" in pool && Array.isArray(pool.processes)) {
        const processes = pool.processes as ProcessInfo[];
        return {
            total: processes.length,
            idle: processes.filter((p) => p.state === "idle").length,
            busy: processes.filter((p) => p.state === "busy").length,
            processes,
        };
    }
    const summary = pool as PoolSummary;
    return {
        total: summary.pool_size ?? 0,
        idle: summary.idle_count ?? 0,
        busy: summary.busy_count ?? 0,
        processes: [] as ProcessInfo[],
    };
}

function getUptimeSeconds(pool: PoolData): number {
    const startedAt = pool.started_at;
    if (!startedAt) return 0;
    return (Date.now() - new Date(startedAt).getTime()) / 1000;
}

function getRuntimeKind(pool: PoolData): RuntimeKind {
    const workerId = pool.worker_id.toLowerCase();
    const hostname = (pool.hostname ?? "").toLowerCase();
    const source = `${workerId} ${hostname}`;

    if (source.includes("aks") || source.includes("k8s")) {
        return "AKS";
    }
    if (source.includes("aca") || source.includes("containerapp")) {
        return "ACA";
    }
    if (/^[a-f0-9]{12}$/.test(workerId) || source.includes("compose") || source.includes("docker")) {
        return "VM";
    }
    return "Unknown";
}

function RuntimeBadge({ pool }: Readonly<{ pool: PoolData }>) {
    const runtime = getRuntimeKind(pool);
    const Icon = runtime === "AKS" ? Boxes : runtime === "ACA" ? Cloud : Server;
    const label =
        runtime === "AKS"
            ? "AKS pod"
            : runtime === "ACA"
                ? "ACA app"
                : runtime === "VM"
                    ? "VM"
                    : "Unknown";

    return (
        <Badge variant="outline" className="gap-1 text-[10px]">
            <Icon className="h-3 w-3" />
            {label}
        </Badge>
    );
}

interface ContainerTableProps {
    pools: PoolData[];
    /** Sorted worker IDs for consistent color assignment (same as chart) */
    workerIds: string[];
}

export function ContainerTable({ pools, workerIds }: ContainerTableProps) {
    const [expanded, setExpanded] = useState<Set<string>>(new Set());

    const toggleExpand = (workerId: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(workerId)) {
                next.delete(workerId);
            } else {
                next.add(workerId);
            }
            return next;
        });
    };

    const colorIndex = (workerId: string) => {
        const idx = workerIds.indexOf(workerId);
        return idx >= 0 ? idx : 0;
    };

    return (
        <div className="border rounded-lg overflow-hidden">
            <Table>
                <TableHeader>
                    <TableRow className="text-xs">
                        <TableHead className="w-8" />
                        <TableHead>Container</TableHead>
                        <TableHead className="w-[100px]">Runtime</TableHead>
                        <TableHead className="w-[80px]">Status</TableHead>
                        <TableHead className="w-[100px]">Forks</TableHead>
                        <TableHead className="w-[200px]">Memory</TableHead>
                        <TableHead className="w-[90px]">Uptime</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {pools.map((pool) => {
                        const isExpanded = expanded.has(pool.worker_id);
                        const counts = getPoolCounts(pool);
                        const memCurrent = "memory_current_bytes" in pool ? (pool.memory_current_bytes ?? -1) : -1;
                        const memMax = "memory_max_bytes" in pool ? (pool.memory_max_bytes ?? -1) : -1;
                        const memPct =
                            memMax > 0 ? (memCurrent / memMax) * 100 : 0;
                        const ci = colorIndex(pool.worker_id);

                        // Build execution map for fork table
                        const execMap = new Map<string, ExecutionRowData>();
                        for (const proc of counts.processes) {
                            if (
                                proc.state === "busy" &&
                                proc.current_execution_id
                            ) {
                                execMap.set(proc.process_id, {
                                    execution_id: proc.current_execution_id,
                                    workflow_name:
                                        proc.current_execution_id.slice(0, 8),
                                    status: "RUNNING",
                                    elapsed_seconds: 0,
                                });
                            }
                        }

                        return (
                            <Fragment key={pool.worker_id}>
                                <TableRow
                                    className="cursor-pointer hover:bg-muted/50"
                                    onClick={() =>
                                        toggleExpand(pool.worker_id)
                                    }
                                >
                                    <TableCell className="w-8 px-2">
                                        {isExpanded ? (
                                            <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                        ) : (
                                            <ChevronRight className="h-4 w-4 text-muted-foreground" />
                                        )}
                                    </TableCell>
                                    <TableCell className="font-medium">
                                        <div className="flex items-center gap-2">
                                            <TooltipProvider delayDuration={200}>
                                                <Tooltip>
                                                    <TooltipTrigger asChild>
                                                        <span
                                                            aria-label="Container chart color"
                                                            className="inline-block w-2 h-2 rounded-sm flex-shrink-0"
                                                            style={{
                                                                backgroundColor:
                                                                    CONTAINER_COLORS[
                                                                        ci %
                                                                            CONTAINER_COLORS.length
                                                                    ],
                                                            }}
                                                        />
                                                    </TooltipTrigger>
                                                    <TooltipContent>
                                                        Color matches this container's
                                                        series in the memory chart above.
                                                    </TooltipContent>
                                                </Tooltip>
                                            </TooltipProvider>
                                            {pool.worker_id}
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        <RuntimeBadge pool={pool} />
                                    </TableCell>
                                    <TableCell>
                                        <Badge
                                            variant={
                                                pool.status === "online"
                                                    ? "secondary"
                                                    : "destructive"
                                            }
                                            className="text-[10px]"
                                        >
                                            {pool.status ?? "offline"}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="text-sm">
                                        {counts.total}{" "}
                                        {counts.busy > 0 && (
                                            <span className="text-xs text-muted-foreground">
                                                ({counts.busy} busy)
                                            </span>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        {memCurrent >= 0 && memMax > 0 ? (
                                            <div className="flex items-center gap-2">
                                                <Progress
                                                    value={memPct}
                                                    className="h-1.5 w-16"
                                                />
                                                <span className="text-xs whitespace-nowrap">
                                                    {formatBytes(memCurrent)} /{" "}
                                                    {formatBytes(memMax)}
                                                </span>
                                            </div>
                                        ) : memCurrent >= 0 ? (
                                            <div className="flex items-center gap-2">
                                                <span className="text-xs whitespace-nowrap">
                                                    {formatBytes(memCurrent)}
                                                </span>
                                                <span className="text-[10px] text-muted-foreground">
                                                    no limit
                                                </span>
                                            </div>
                                        ) : (
                                            <span className="text-xs text-muted-foreground">
                                                N/A
                                            </span>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-xs text-muted-foreground">
                                        {formatUptime(getUptimeSeconds(pool))}
                                    </TableCell>
                                </TableRow>
                                <AnimatePresence>
                                    {isExpanded &&
                                        counts.processes.length > 0 && (
                                            <TableRow>
                                                <TableCell
                                                    colSpan={7}
                                                    className="p-0 bg-muted/30"
                                                >
                                                    <motion.div
                                                        initial={{
                                                            height: 0,
                                                            opacity: 0,
                                                        }}
                                                        animate={{
                                                            height: "auto",
                                                            opacity: 1,
                                                        }}
                                                        exit={{
                                                            height: 0,
                                                            opacity: 0,
                                                        }}
                                                        transition={{
                                                            duration: 0.2,
                                                        }}
                                                        className="overflow-hidden"
                                                    >
                                                        <div className="px-4 py-3 pl-10">
                                                            <ForkTable
                                                                workerId={
                                                                    pool.worker_id
                                                                }
                                                                processes={
                                                                    counts.processes
                                                                }
                                                                executions={
                                                                    execMap
                                                                }
                                                                containerMemoryMax={
                                                                    memMax > 0
                                                                        ? memMax
                                                                        : undefined
                                                                }
                                                            />
                                                        </div>
                                                    </motion.div>
                                                </TableCell>
                                            </TableRow>
                                        )}
                                </AnimatePresence>
                            </Fragment>
                        );
                    })}
                </TableBody>
            </Table>
        </div>
    );
}
