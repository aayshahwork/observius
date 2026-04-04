// Example: Report a completed browser agent run to Pokant.
// Uses only the Go standard library — copy-paste into your project.
//
// Usage:
//   POKANT_API_KEY=cu_test_testkey1234567890abcdef12 go run reporter.go
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"time"
)

type step struct {
	StepNumber int    `json:"step_number"`
	ActionType string `json:"action_type"`
	Description string `json:"description"`
	DurationMs int    `json:"duration_ms"`
	Success    bool   `json:"success"`
	TokensIn   int    `json:"tokens_in"`
	TokensOut  int    `json:"tokens_out"`
}

type ingestPayload struct {
	TaskDescription string `json:"task_description"`
	Status          string `json:"status"`
	ExecutorMode    string `json:"executor_mode"`
	DurationMs      int    `json:"duration_ms"`
	TotalTokensIn   int    `json:"total_tokens_in"`
	TotalTokensOut  int    `json:"total_tokens_out"`
	Steps           []step `json:"steps"`
}

func main() {
	apiURL := os.Getenv("POKANT_API_URL")
	if apiURL == "" {
		apiURL = "http://localhost:8000"
	}
	apiKey := os.Getenv("POKANT_API_KEY")
	if apiKey == "" {
		apiKey = "cu_test_testkey1234567890abcdef12"
	}

	payload := ingestPayload{
		TaskDescription: "Example: extract page title from example.com",
		Status:          "completed",
		ExecutorMode:    "sdk",
		DurationMs:      5000,
		Steps: []step{
			{StepNumber: 0, ActionType: "navigate", Description: "goto(https://example.com)", DurationMs: 2000, Success: true},
			{StepNumber: 1, ActionType: "extract", Description: "Extracted page title: Example Domain", DurationMs: 3000, Success: true},
		},
	}

	body, _ := json.Marshal(payload)
	req, _ := http.NewRequest("POST", apiURL+"/api/v1/tasks/ingest", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", apiKey)

	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		fmt.Fprintf(os.Stderr, "request failed: %v\n", err)
		os.Exit(1)
	}
	defer resp.Body.Close()

	var result map[string]any
	json.NewDecoder(resp.Body).Decode(&result)
	fmt.Printf("Status: %d\nTask ID: %v\n", resp.StatusCode, result["task_id"])
}
