"use client";

import { useState, useCallback } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

interface JsonEditorProps {
  label?: string;
  value: string;
  onChange: (value: string, parsed: Record<string, unknown> | null) => void;
  placeholder?: string;
}

export function JsonEditor({
  label,
  value,
  onChange,
  placeholder = '{\n  "key": "value"\n}',
}: JsonEditorProps) {
  const [error, setError] = useState<string | null>(null);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const raw = e.target.value;
      onChange(raw, null);
    },
    [onChange]
  );

  const handleBlur = useCallback(() => {
    if (!value.trim()) {
      setError(null);
      onChange(value, null);
      return;
    }
    try {
      const parsed = JSON.parse(value);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setError("Must be a JSON object");
        return;
      }
      setError(null);
      onChange(value, parsed);
    } catch {
      setError("Invalid JSON");
    }
  }, [value, onChange]);

  return (
    <div className="space-y-2">
      {label && <Label>{label}</Label>}
      <Textarea
        value={value}
        onChange={handleChange}
        onBlur={handleBlur}
        placeholder={placeholder}
        className="font-mono text-xs"
        rows={6}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
