"use client";

import { DataTable, StatusBadge } from "@/components/ui";
import { fmtTime, humanize, shortDigest } from "@/lib/format";
import { SafeLink } from "../SafeLink";
import { Callout, defineDomainComponent, Facts, Mono, Muted, Panel, SubHeading, type DomainViewProps } from "./shared";

function GitOpsHandoffPreviewView({ data: d, panel }: DomainViewProps<"GitOpsHandoffPreview">) {
  const b = d.bundle;
  return (
    <Panel {...panel} meta={d.package_status ? <StatusBadge value={d.package_status} /> : undefined}>
      <Callout tone="warning" title="Approved or exported is not deployed" testId="ws-not-deployed">
        A bundle is a handoff to Cloud Engineering&apos;s pipeline. Enforcement is verified only by fresh observations.
      </Callout>
      {b ? (
        <>
          <Facts items={[
            ["Bundle", <SafeLink key="b" href={b.href}>{b.id}</SafeLink>],
            ["Kind", humanize(b.kind)],
            ["Status banner", b.banner],
            ["Bundle digest", <Mono key="d" title={b.bundle_digest}>{shortDigest(b.bundle_digest)}</Mono>],
            ["Exported", fmtTime(b.exported_at)],
          ]} />
          <div>
            <SubHeading hint={`(${b.files.length})`}>Files</SubHeading>
            <DataTable rows={b.files} rowKey={(f) => f.path} caption="Bundle files" empty="The bundle lists no files." columns={[
              { key: "p", header: "Path", render: (f) => <Mono>{f.path}</Mono> },
              { key: "s", header: "SHA-256", render: (f) => <Mono title={f.sha256}>{shortDigest(f.sha256)}</Mono> },
            ]} />
          </div>
        </>
      ) : <Muted>No handoff bundle has been exported.</Muted>}
      <Muted>{d.note}</Muted>
    </Panel>
  );
}

export const GitOpsHandoffPreview = defineDomainComponent("GitOpsHandoffPreview", GitOpsHandoffPreviewView);
