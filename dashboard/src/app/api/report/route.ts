import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { join } from "path";

// intel_report.json sits one level above the dashboard/ directory.
const REPORT_PATH = join(process.cwd(), "..", "intel_report.json");

export async function GET() {
  try {
    const raw = readFileSync(REPORT_PATH, "utf-8");
    const data = JSON.parse(raw);
    return NextResponse.json({ status: "complete", data });
  } catch (err: unknown) {
    // ENOENT → report not yet written; any other error → surface it.
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      return NextResponse.json({ status: "pending" });
    }
    console.error("report route error:", err);
    return NextResponse.json(
      { status: "error", message: String(err) },
      { status: 500 }
    );
  }
}
