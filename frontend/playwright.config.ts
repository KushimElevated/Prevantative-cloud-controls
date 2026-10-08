import { defineConfig } from "@playwright/test";
import fs from "node:fs";

const bundled = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  retries: 0,
  // Specs share one backend database and change its state, so they run one at a time in file order.
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "off",
    launchOptions: fs.existsSync(bundled) ? { executablePath: bundled } : {},
  },
});
