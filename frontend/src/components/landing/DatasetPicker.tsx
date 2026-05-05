import React, { useState } from "react";
import { Plus, ExternalLink } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { DatasetItem } from "@/lib/replayApi";

interface DatasetPickerProps {
  datasets: DatasetItem[];
  loading: boolean;
  onPickExisting: (repoId: string) => void;
  onCreateNew: (name: string) => void;
  onOpenCustom: (repoId: string) => void;
  children: React.ReactNode;
}

const REPO_ID_RE = /^[\w.\-]+\/[\w.\-]+$/;
const NAME_RE = /^[A-Za-z0-9._-]+$/;

const DatasetPicker: React.FC<DatasetPickerProps> = ({
  datasets,
  loading,
  onPickExisting,
  onCreateNew,
  onOpenCustom,
  children,
}) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const trimmed = query.trim();
  const matchesExisting = datasets.some(
    (d) => d.repo_id.toLowerCase() === trimmed.toLowerCase(),
  );
  const isRepoId = REPO_ID_RE.test(trimmed);
  const isName = NAME_RE.test(trimmed) && !trimmed.includes("/");
  const canCreate = trimmed.length > 0 && isName && !matchesExisting;
  const canOpenCustom = isRepoId && !matchesExisting;

  const reset = () => {
    setQuery("");
    setOpen(false);
  };

  const handlePick = (repoId: string) => {
    onPickExisting(repoId);
    reset();
  };

  const handleCreate = () => {
    if (!canCreate) return;
    onCreateNew(trimmed);
    reset();
  };

  const handleOpenCustom = () => {
    if (!canOpenCustom) return;
    onOpenCustom(trimmed);
    reset();
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        className="w-[320px] p-0 bg-gray-800 border-gray-700 text-white"
        align="end"
      >
        <Command className="bg-gray-800">
          <CommandInput
            placeholder="Search, type a new name, or org/name…"
            value={query}
            onValueChange={(v) => setQuery(v.replace(/[^A-Za-z0-9._\-/]/g, "_"))}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              if (canCreate) {
                e.preventDefault();
                handleCreate();
              } else if (canOpenCustom) {
                e.preventDefault();
                handleOpenCustom();
              }
            }}
            className="text-white"
          />
          <CommandList>
            {datasets.length === 0 && !canCreate && !canOpenCustom && (
              <CommandEmpty className="py-4 text-sm text-gray-400 text-center">
                {loading
                  ? "Loading datasets…"
                  : "No datasets yet. Type a name to create one."}
              </CommandEmpty>
            )}
            {datasets.length > 0 && (
              <CommandGroup heading="Existing">
                {datasets.map((d) => (
                  <CommandItem
                    key={d.repo_id}
                    value={d.repo_id}
                    onSelect={() => handlePick(d.repo_id)}
                    className="text-white aria-selected:bg-gray-700"
                  >
                    <span className="flex-1 truncate">{d.repo_id}</span>
                    {d.private && (
                      <span className="text-xs text-amber-400">private</span>
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {canCreate && (
              <CommandGroup heading="New">
                <CommandItem
                  value={`__create__${trimmed}`}
                  onSelect={handleCreate}
                  className="text-white aria-selected:bg-gray-700"
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Create &quot;{trimmed}&quot;
                </CommandItem>
              </CommandGroup>
            )}
            {canOpenCustom && (
              <CommandGroup heading="Custom repo">
                <CommandItem
                  value={`__open__${trimmed}`}
                  onSelect={handleOpenCustom}
                  className="text-white aria-selected:bg-gray-700"
                >
                  <ExternalLink className="mr-2 h-4 w-4" />
                  Open &quot;{trimmed}&quot; in viewer
                </CommandItem>
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
};

export default DatasetPicker;
