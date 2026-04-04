/**
 * Pokant Reporter — send browser agent results to Pokant dashboard.
 * Works with any TypeScript/JavaScript agent framework.
 * Zero dependencies — uses the built-in fetch() API.
 *
 * Usage:
 *   const reporter = new PokantReporter({ apiUrl: "http://localhost:8000", apiKey: "..." });
 *   reporter.start("Extract pricing from portal");
 *   reporter.recordStep({ actionType: "navigate", description: "Opened portal" });
 *   reporter.recordStep({ actionType: "extract", description: "Got prices", tokensIn: 1500 });
 *   await reporter.complete();
 */

interface PokantConfig {
  apiUrl: string;
  apiKey: string;
}

interface StepData {
  actionType?: string;
  description?: string;
  screenshotBase64?: string | null;
  tokensIn?: number;
  tokensOut?: number;
  success?: boolean;
  error?: string | null;
  durationMs?: number;
}

interface IngestStep {
  step_number: number;
  action_type: string;
  description: string;
  screenshot_base64: string | null;
  tokens_in: number;
  tokens_out: number;
  success: boolean;
  error: string | null;
  duration_ms: number;
}

class PokantReporter {
  private config: PokantConfig;
  private taskDescription: string = "";
  private steps: IngestStep[] = [];
  private startTime: number = 0;
  private stepCount: number = 0;
  private lastStepTime: number = 0;

  constructor(config: PokantConfig) {
    this.config = config;
  }

  start(taskDescription: string): void {
    this.taskDescription = taskDescription;
    this.startTime = Date.now();
    this.lastStepTime = this.startTime;
    this.steps = [];
    this.stepCount = 0;
  }

  recordStep(step: StepData): void {
    const now = Date.now();
    this.steps.push({
      step_number: this.stepCount++,
      action_type: step.actionType || "unknown",
      description: step.description || "",
      screenshot_base64: step.screenshotBase64 || null,
      tokens_in: step.tokensIn || 0,
      tokens_out: step.tokensOut || 0,
      success: step.success !== false,
      error: step.error || null,
      duration_ms: step.durationMs || (now - this.lastStepTime),
    });
    this.lastStepTime = now;
  }

  async complete(): Promise<string | null> {
    return this._report("completed");
  }

  async fail(error: string, errorCategory?: string): Promise<string | null> {
    return this._report("failed", error, errorCategory);
  }

  private async _report(
    status: string,
    error?: string,
    errorCategory?: string,
  ): Promise<string | null> {
    try {
      const totalTokensIn = this.steps.reduce((sum, s) => sum + s.tokens_in, 0);
      const totalTokensOut = this.steps.reduce((sum, s) => sum + s.tokens_out, 0);

      const payload = {
        task_description: this.taskDescription,
        status,
        executor_mode: "sdk",
        duration_ms: Date.now() - this.startTime,
        total_tokens_in: totalTokensIn,
        total_tokens_out: totalTokensOut,
        cost_cents: 0,
        error_message: error || null,
        error_category: errorCategory || null,
        steps: this.steps,
      };

      const response = await fetch(
        `${this.config.apiUrl}/api/v1/tasks/ingest`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-API-Key": this.config.apiKey,
          },
          body: JSON.stringify(payload),
        },
      );

      if (!response.ok) {
        console.warn(`Pokant reporting failed: ${response.status}`);
        return null;
      }

      const data = await response.json();
      return data.task_id;
    } catch (e) {
      console.warn("Pokant reporting failed:", e);
      return null;
    }
  }
}

export { PokantReporter, PokantConfig, StepData };
