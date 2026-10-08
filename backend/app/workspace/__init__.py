"""Optional A2UI-powered AI Control Workspace.

A presentation and interaction layer over the existing domain services. It owns no domain state:
every number, status and decision shown comes from the same services the Classic Experience uses,
evaluated with the requesting principal's authorization. State changes go through the existing
governed services after explicit human confirmation.
"""
