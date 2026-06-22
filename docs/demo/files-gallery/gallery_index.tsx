import { files, useFiles, useState, useRef } from "bifrost";

const LOCATION = "shared";
const PREFIX = "gallery";

export default function Gallery() {
	const { files: names, loading, denied, empty, refetch } = useFiles(PREFIX, {
		location: LOCATION,
	});
	const [status, setStatus] = useState<string>("");
	const [busy, setBusy] = useState(false);
	const [previews, setPreviews] = useState<Record<string, string>>({});
	const inputRef = useRef<HTMLInputElement | null>(null);

	async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
		const file = e.target.files?.[0];
		if (!file) return;
		setBusy(true);
		setStatus(`Uploading ${file.name}…`);
		try {
			await files.upload(`${PREFIX}/${file.name}`, file, { location: LOCATION });
			setStatus(`Uploaded ${file.name}`);
			await refetch();
		} catch (err) {
			setStatus(`Upload denied: ${(err as Error).name}`);
		} finally {
			setBusy(false);
			if (inputRef.current) inputRef.current.value = "";
		}
	}

	async function preview(name: string) {
		try {
			const blob = await files.download(name, { location: LOCATION });
			setPreviews((p) => ({ ...p, [name]: URL.createObjectURL(blob) }));
		} catch (err) {
			setStatus(`Download denied for ${name}: ${(err as Error).name}`);
		}
	}

	async function remove(name: string) {
		setBusy(true);
		try {
			await files.delete(name, { location: LOCATION });
			setStatus(`Deleted ${name}`);
			setPreviews((p) => {
				const next = { ...p };
				delete next[name];
				return next;
			});
			await refetch();
		} catch (err) {
			setStatus(`Delete denied for ${name}: ${(err as Error).name}`);
		} finally {
			setBusy(false);
		}
	}

	return (
		<div style={{ maxWidth: 920, margin: "0 auto", padding: "32px 24px" }}>
			<h1 style={{ fontSize: 28, fontWeight: 700, margin: 0 }}>📁 Shared Gallery</h1>
			<p style={{ color: "#9aa0a6", marginTop: 6 }}>
				Direct browser-to-storage via the Bifrost Files SDK — no workflow runs. Access is governed by the <code>shared/gallery</code> file policy.
			</p>

			<div style={{ display: "flex", gap: 12, alignItems: "center", margin: "20px 0" }}>
				<input ref={inputRef} type="file" onChange={onUpload} disabled={busy} data-testid="upload-input" />
				<button onClick={() => refetch()} disabled={busy} style={btn}>Refresh</button>
				{status && <span data-testid="status" style={{ color: "#8ab4f8", fontSize: 13 }}>{status}</span>}
			</div>

			{loading && <p>Loading…</p>}
			{denied && <p data-testid="denied" style={{ color: "#f28b82" }}>🔒 You don't have access to this gallery (policy denied).</p>}
			{empty && !denied && <p style={{ color: "#9aa0a6" }}>No files yet — upload one above.</p>}

			<div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 16, marginTop: 16 }} data-testid="grid">
				{names.map((name) => (
					<div key={name} style={card}>
						{previews[name] ? (
							<img src={previews[name]} alt={name} style={{ width: "100%", height: 120, objectFit: "cover", borderRadius: 6 }} />
						) : (
							<button onClick={() => preview(name)} style={{ ...btn, width: "100%", height: 120 }}>Preview</button>
						)}
						<div style={{ fontSize: 12, marginTop: 8, wordBreak: "break-all" }}>{name.replace(`${PREFIX}/`, "")}</div>
						<button onClick={() => remove(name)} disabled={busy} style={{ ...btn, marginTop: 6, color: "#f28b82" }}>Delete</button>
					</div>
				))}
			</div>
		</div>
	);
}

const btn: React.CSSProperties = {
	background: "#1f1f26",
	color: "#e7e7ea",
	border: "1px solid #2c2c36",
	borderRadius: 6,
	padding: "6px 12px",
	cursor: "pointer",
	fontSize: 13,
};

const card: React.CSSProperties = {
	background: "#13131a",
	border: "1px solid #23232c",
	borderRadius: 10,
	padding: 12,
};
