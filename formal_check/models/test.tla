---- MODULE test ----
EXTENDS TLC

VARIABLES status, approved, cancelled

vars == <<status, approved, cancelled>>

Init == /\ status = "Created" /\ approved = FALSE /\ cancelled = FALSE

Approve == /\ status = "Created" /\ status' = "Approved" /\  approved' = TRUE /\ UNCHANGED <<cancelled>>

Cancel == /\ status \in {"Approved", "Created"} /\ status' = "Canceled" /\ UNCHANGED <<cancelled, approved>>

Execute == /\ approved = TRUE /\ cancelled = FALSE /\ status' = "Executed" /\ UNCHANGED <<cancelled, approved>>

Next == \/ Approve \/ Cancel \/ Execute

Spec == Init /\ [][Next]_vars

Invariant == \/ (cancelled = TRUE /\ status = "Canceled") \/ (cancelled = FALSE /\ status \in {"Created", "Approved", "Executed"})
====