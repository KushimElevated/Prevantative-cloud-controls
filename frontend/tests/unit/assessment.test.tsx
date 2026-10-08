import { render, screen } from "@testing-library/react";
import { AssessmentSummary } from "@/components/control/AssessmentSection";
import { NextDecision } from "@/components/control/NextDecision";

vi.mock("next/link", () => ({ default: ({ href, children }: any) => <a href={href}>{children}</a> }));

const baseRun = {
  id: "asmt_1", status: "COMPLETED", disclosure: "Evaluated fixtures only.",
  confidence: {
    configuration: { category: "MEDIUM", reasons: ["Fixture data."] },
    request_impact: { category: "NONE", reasons: ["No request fixtures."] },
    readiness: { category: "LOW", reasons: ["Missing evidence."] },
  },
  rollup: {
    configuration: { counts: { COMPLIANT: 1, NON_COMPLIANT: 5, UNKNOWN: 1, NOT_APPLICABLE: 1 }, total_resources_evaluated: 8 },
    exceptions: { counts: { NONE: 3, PENDING: 1, APPROVED_UNAPPLIED: 1, EFFECTIVE: 1, EXPIRED: 1, UNSUPPORTED: 0 } },
    request_impact: { evidence_present: false, counts: null, potentially_blocked_operations: "UNKNOWN",
                      note: "No request evidence was supplied." },
    readiness: { by_application: { "retail-catalog": "READY" } },
    baseline_vs_proposed: { baseline_coverage_counts: { AUDIT_ONLY: 7 }, newly_preventive_resources: 7 },
  },
};

describe("AssessmentSummary", () => {
  it("reports unknown request impact (not zero) when no request evidence exists", () => {
    render(<AssessmentSummary run={baseRun} />);
    expect(screen.getByText(/potentially blocked operations are/i)).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
    expect(screen.queryByText("Predicted denied")).toBeNull();
  });

  it("links each configuration count to filtered results", () => {
    render(<AssessmentSummary run={baseRun} />);
    const link = screen.getByText("Non compliant").closest("a");
    expect(link).toHaveAttribute("href", "/assessments/asmt_1?configuration_result=NON_COMPLIANT");
  });

  it("shows an unsupported run without counts", () => {
    render(<AssessmentSummary run={{ ...baseRun, status: "UNSUPPORTED", rollup: { status: "UNSUPPORTED", reason: "No evaluator." } }} />);
    expect(screen.getByText(/No evaluator/)).toBeInTheDocument();
    expect(screen.queryByText("Compliant")).toBeNull();
  });
});

describe("NextDecision", () => {
  it("names the next decision and its owner", () => {
    render(<NextDecision detail={{
      next_decision: { decision: "Security approval of the change package", owner_role: "SECURITY_APPROVER", detail: "sha256:abc" },
      pending_decisions: [{ decision: "Security approval of the change package", owner_role: "SECURITY_APPROVER" }],
    }} />);
    expect(screen.getByTestId("next-decision")).toHaveTextContent("Security approval of the change package");
    expect(screen.getByTestId("next-decision")).toHaveTextContent("Owner: Security approver");
  });
});
