import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function GitHubTokenField({ value, saved, valid, error, pending, disabled, onChange, onValidate }: {
 value: string; saved: boolean; valid: boolean | null; error: string | null; pending: boolean; disabled: boolean;
 onChange: (value: string) => void; onValidate: () => void;
}) {
 return <div className="min-w-0 space-y-3">
  <Label htmlFor="github-token">GitHub Personal Access Token</Label>
  <div className="flex flex-col gap-2 sm:flex-row">
   <Input id="github-token" type="password" autoComplete="off" className="min-h-11 min-w-0" placeholder="Enter a token" value={value} disabled={pending || disabled} aria-describedby="github-token-help" onChange={(e) => onChange(e.target.value)} />
   <Button type="button" variant="outline" className="min-h-11 shrink-0" disabled={pending || disabled || !value.trim()} onClick={onValidate}>{pending ? "Validating…" : "Validate"}</Button>
  </div>
  {valid === false && <p role="alert" className="text-sm text-destructive">{error}</p>}
  {valid === true && value && <p role="status" className="text-sm text-[var(--bf-success)]">Token validated.</p>}
  <p id="github-token-help" className="text-sm text-muted-foreground">{saved ? "A token is saved. Enter and validate a new token to replace it. " : ""}Create a token with repository access in <a href="https://github.com/settings/tokens/new" target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">GitHub token settings</a>.</p>
 </div>;
}
