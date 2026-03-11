"use client";

import { useState } from "react";
import { Menu, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sidebar } from "./sidebar";

export function MobileHeader() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <header className="flex h-14 items-center border-b px-4 lg:hidden">
        <Button variant="ghost" size="icon-sm" onClick={() => setOpen(true)}>
          <Menu className="size-5" />
        </Button>
        <span className="ml-3 text-sm font-semibold">ComputerUse.dev</span>
      </header>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/40 lg:hidden"
            onClick={() => setOpen(false)}
          />
          <div className="fixed inset-y-0 left-0 z-50 lg:hidden">
            <div className="relative">
              <Sidebar />
              <Button
                variant="ghost"
                size="icon-sm"
                className="absolute right-2 top-3"
                onClick={() => setOpen(false)}
              >
                <X className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
