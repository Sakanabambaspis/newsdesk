# Elicitation playbook

Type: grilling
Status: open
Blocked by: 14

## Question

(Experimental — scope it, then decide ship or defer.) What must the tool
layer expose for a harness agent to run preference-elicitation conversations —
compiling fuzzy user vocabulary into a rubric through: draft rubric →
score-preview on a sample set → diff vs the current rubric → save as a new
version?

Decide: the minimal tool additions (if any) beyond ticket 13's surface; the
playbook's shape (where it is documented — harness-side skill or newsdesk
docs); and whether this ships in W6 or the fog absorbs it. The conversation
itself stays in the harness; newsdesk only makes rubrics cheap to create,
validate, simulate, and diff.
