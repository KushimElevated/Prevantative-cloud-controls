import { render, screen } from "@testing-library/react";
import { Meter, StatusBadge, StatTile } from "@/components/ui";

describe("StatusBadge", () => {
  it("pairs an icon with a text label so status is never color-only", () => {
    render(<StatusBadge value="NON_COMPLIANT" />);
    const badge = screen.getByText("Non compliant").closest("[data-status]");
    expect(badge).toHaveAttribute("data-status", "NON_COMPLIANT");
    expect(badge?.querySelector('[aria-hidden="true"]')?.textContent).toBe("▲");
  });

  it("renders a dash for missing values", () => {
    const { container } = render(<StatusBadge value={null} />);
    expect(container.textContent).toBe("-");
  });

  it("treats unknown as a warning, not as compliant", () => {
    render(<StatusBadge value="UNKNOWN" />);
    expect(screen.getByText("Unknown").closest("[data-status]")?.querySelector('[aria-hidden="true"]')?.textContent).toBe("!");
  });
});

describe("Meter", () => {
  it("shows N/A for an empty denominator instead of 0%", () => {
    render(<Meter numerator={0} denominator={0} label="Verified protected" />);
    expect(screen.getByText(/N\/A/)).toBeInTheDocument();
    expect(screen.queryByRole("meter")).toBeNull();
  });

  it("states numerator and denominator", () => {
    render(<Meter numerator={1} denominator={7} label="Verified protected" />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuemax", "7");
    expect(screen.getByText("1 of 7 (14%)")).toBeInTheDocument();
  });
});

describe("StatTile", () => {
  it("links a summary count to its underlying rows", () => {
    render(<StatTile label="Non compliant" value={5} href="/assessments/a1?configuration_result=NON_COMPLIANT" />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/assessments/a1?configuration_result=NON_COMPLIANT");
  });
});
