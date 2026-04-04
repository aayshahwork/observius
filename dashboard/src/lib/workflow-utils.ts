import type { CompiledWorkflow, CompiledStep } from "./types";

const PARAM_RE = /\{\{(\w+)\}\}/g;

function stepToPlaywrightLine(step: CompiledStep): string {
  const selector = step.selectors?.[0]?.value ?? "";

  if (step.action_type === "goto") {
    const url = step.pre_url || "https://example.com";
    return `await page.goto("${url}");`;
  }

  if (step.action_type === "fill") {
    const value = step.fill_value_template;
    const params = [...value.matchAll(PARAM_RE)].map((m) => m[1]);
    if (params.length > 0) {
      let tpl = value;
      for (const p of params) {
        tpl = tpl.replace(`{{${p}}}`, `\${PARAMS["${p}"]}`);
      }
      return `await page.fill("${selector}", \`${tpl}\`);`;
    }
    return `await page.fill("${selector}", "${value}");`;
  }

  if (step.action_type === "select_option") {
    return `await page.selectOption("${selector}", "");`;
  }

  if (step.action_type === "press") {
    return `await page.press("${selector}", "Enter");`;
  }

  if (step.action_type === "scroll") {
    return `await page.evaluate("window.scrollBy(0, 300)");`;
  }

  if (step.action_type === "wait") {
    return `await page.waitForTimeout(${step.timeout_ms});`;
  }

  if (step.action_type === "extract") {
    return `await page.textContent("${selector}");`;
  }

  if (step.action_type === "dblclick") {
    return `await page.dblclick("${selector}");`;
  }

  if (step.action_type === "right_click") {
    return `await page.click("${selector}", { button: "right" });`;
  }

  return `await page.click("${selector}");`;
}

export function generatePlaywrightScript(workflow: CompiledWorkflow): string {
  const lines: string[] = [];

  lines.push(`"""Auto-generated Playwright script.`);
  lines.push(`Workflow: ${workflow.name}`);
  if (workflow.start_url) {
    lines.push(`Start URL: ${workflow.start_url}`);
  }
  lines.push(`"""`);
  lines.push("");
  lines.push("import asyncio");
  lines.push("from playwright.async_api import async_playwright");
  lines.push("");

  const paramNames = Object.keys(workflow.parameters).sort();
  lines.push("PARAMS = {");
  for (const p of paramNames) {
    lines.push(`    "${p}": "",  # TODO: fill in`);
  }
  lines.push("}");
  lines.push("");
  lines.push("");
  lines.push("async def main():");
  lines.push("    async with async_playwright() as p:");
  lines.push("        browser = await p.chromium.launch(headless=False)");
  lines.push("        page = await browser.new_page()");
  lines.push("");

  workflow.steps.forEach((step, i) => {
    lines.push(`        # Step ${i + 1}: ${step.intent}`);
    lines.push(`        ${stepToPlaywrightLine(step)}`);
    if (step.timeout_ms > 0) {
      lines.push(`        await page.wait_for_timeout(${step.timeout_ms})`);
    }
    lines.push("");
  });

  lines.push("        await browser.close()");
  lines.push("");
  lines.push("");
  lines.push('if __name__ == "__main__":');
  lines.push("    asyncio.run(main())");
  lines.push("");

  return lines.join("\n");
}
