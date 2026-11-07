import {open} from 'node:fs/promises';

// Reserve before any effects; keep the same inode through subsequent updates.
// A failed invocation leaves its reservation for explicit reconciliation.
export async function withExclusiveReceipt(path, invoke) {
  const file = await open(path, 'wx', 0o600);
  try {
    const save = async report => {
      const bytes = Buffer.from(JSON.stringify(report, null, 2) + '\n');
      let offset = 0;
      while (offset < bytes.length) {
        const {bytesWritten} = await file.write(bytes, offset, bytes.length - offset, offset);
        if (!bytesWritten) throw new Error('Receipt write made no progress');
        offset += bytesWritten;
      }
      await file.truncate(bytes.length);
      await file.sync();
    };
    await save({state: 'reserved', completion_verified: false});
    return await invoke(save);
  } finally {
    await file.close();
  }
}
