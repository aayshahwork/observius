"use client";

import { useState, useEffect } from "react";
import { Menu, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AlertBell } from "@/components/alert-bell";
import { Sidebar } from "./sidebar";

export function MobileHeader() {
  const [open, setOpen] = useState(false);

  // Prevent body scroll when sidebar is open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <>
      <header className="flex h-14 items-center border-b px-4">
        <div className="flex items-center lg:hidden">
          <Button variant="ghost" size="icon-sm" onClick={() => setOpen(true)}>
            <Menu className="size-5" />
          </Button>
          <span className="ml-3 text-sm font-semibold">Pokant</span>
        </div>
        <div className="ml-auto">
          <AlertBell />
        </div>
      </header>

      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/50 transition-opacity duration-200 lg:hidden ${
          open
            ? "pointer-events-auto opacity-100"
            : "pointer-events-none opacity-0"
        }`}
        onClick={() => setOpen(false)}
      />

      {/* Sidebar drawer */}
      <div
        className={`fixed inset-y-0 left-0 z-50 transition-transform duration-200 ease-out lg:hidden ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
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
  );
}
