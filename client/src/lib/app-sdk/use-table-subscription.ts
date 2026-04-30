import { useEffect, useLayoutEffect, useRef } from "react";
import { tables, type TableChangeEvent } from "./tables";

export function useTableSubscription(
  tableId: string,
  onEvent: (evt: TableChangeEvent) => void,
): void {
  const callbackRef = useRef(onEvent);
  useLayoutEffect(() => {
    callbackRef.current = onEvent;
  });
  useEffect(() => {
    const off = tables.subscribe(tableId, (evt) => callbackRef.current(evt));
    return off;
  }, [tableId]);
}
