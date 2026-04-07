"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, HeartPulse, ListTodo, FileCode2, Key, BarChart3, Settings, Sun, Moon, Monitor, LogOut } from "lucide-react";
import { Separator } from "@/components/ui/separator";
import { useTheme } from "@/contexts/theme-context";
import { useAuth } from "@/contexts/auth-context";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard },
  { href: "/health", label: "Health", icon: HeartPulse },
  { href: "/tasks", label: "Tasks", icon: ListTodo },
  { href: "/scripts", label: "Scripts", icon: FileCode2 },
  { href: "/sessions", label: "Sessions", icon: Key },
  { href: "/usage", label: "Usage", icon: BarChart3 },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar({ className }: { className?: string }) {
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();
  const { logout } = useAuth();

  const cycleTheme = () => {
    const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
    setTheme(next);
  };

  const ThemeIcon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;

  return (
    <aside
      className={cn(
        "flex h-screen w-60 flex-col border-r bg-sidebar text-sidebar-foreground",
        className
      )}
    >
      <div className="flex h-14 items-center px-4">
        <Link href="/overview" className="text-base font-semibold tracking-tight">
          Pokant
        </Link>
      </div>

      <Separator />

      <nav className="flex-1 space-y-1 px-2 py-3">
        {navItems.map((item) => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-all duration-200",
                isActive
                  ? "border-l-[3px] border-l-brand bg-sidebar-accent pl-[9px] text-sidebar-accent-foreground"
                  : "border-l-[3px] border-l-transparent pl-[9px] text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              )}
            >
              <item.icon className="size-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="space-y-1 px-2 pb-4">
        <Separator className="mb-3" />
        <button
          onClick={cycleTheme}
          className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        >
          <ThemeIcon className="size-4" />
          {theme === "light" ? "Light" : theme === "dark" ? "Dark" : "System"}
        </button>
        <button
          onClick={logout}
          className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        >
          <LogOut className="size-4" />
          Log out
        </button>
      </div>
    </aside>
  );
}
