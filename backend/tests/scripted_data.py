"""TEST-ONLY scripted stand-in for the AI, with a full set of hand-written extraction results.

This exists so the board, Ask box, brief and UI can be tested and photographed without a key or network.
It is NOT model output and must never be presented as such: the demo uses real Gemini results
(or the snapshot saved from a real run). Every quote below is copied from the sample documents, and
the extractor's own quote check enforces that: a bad quote would be rejected and a test would fail.
"""
import json
import re


def _find(prompt: str, kind: str, needle: str) -> int:
    m = re.search(rf"^#(\d+) \| {kind}[^|]*\|.*?{needle}", prompt, re.M | re.I)
    assert m, f"scripted data looked for a tracked {kind} matching {needle!r}"
    return int(m.group(1))


def extraction_ops(prompt: str) -> list[dict]:
    C = lambda **kw: {"op": "create", **kw}
    U = lambda i, **kw: {"op": "update", "item_id": i, **kw}
    M = lambda i, q: {"op": "mention", "item_id": i, "quote": q}

    if "Title: Statement of Work" in prompt:
        return [
            C(item_type="decision", summary="Run one parallel payroll cycle before go-live",
              quote="ADP will conduct one (1) parallel payroll cycle before go-live."),
            C(item_type="opportunity", summary="Add benefits administration under a separate agreement",
              quote="Benefits administration is excluded from this Statement of Work and may be added under a separate agreement."),
        ]
    if "Title: Riverbend Health - Implementation Kickoff" in prompt:
        return [
            C(item_type="commitment", summary="Priya Raman: deliver the complete employee data file", owner="Priya", due_date="2026-09-22",
              quote="We can have it to you by September 22."),
            C(item_type="commitment", summary="Sandra Okafor: set up SFTP access", owner="Sandra", due_date="2026-09-18",
              quote="I'll have SFTP access ready by September 18."),
            C(item_type="commitment", summary="Tomás Rivera: finish state and local tax configuration", owner="Tomás", due_date="2026-10-01",
              quote="I can have the state and local tax configuration done by October 1, as long as the file arrives in time."),
            C(item_type="commitment", summary="Dana Whitfield: send the detailed implementation plan to Priya", owner="Dana", due_date="2026-09-11",
              quote="I'll send the detailed implementation plan to Priya by Friday, September 11."),
            C(item_type="commitment", summary="Check whether the timeclock vendor offers an API for time import",
              quote="Someone should check with the vendor."),
            C(item_type="decision", summary="Biweekly pay with Friday pay dates", rationale="Keeps the schedule so nobody's pay date changes",
              quote="So, biweekly with Friday pay dates."),
            C(item_type="decision", summary="Ohio uses 8-and-80 overtime; Pennsylvania uses weekly 40",
              rationale="Changing Ohio nurses' overtime at go-live would change their paychecks overnight",
              quote="Okay, Ohio on 8-and-80 and Pennsylvania on weekly 40."),
            M(_find(prompt, "opportunity", "benefits"), "Eventually we'd like it in the same system, but not in this phase."),
        ]
    if "Title: Implementation plan and next steps" in prompt:
        data, sftp, tax = (_find(prompt, "commitment", n) for n in ("employee data file", "SFTP", "tax configuration"))
        return [
            M(data, "- Sep 22: Complete employee data file delivered (Priya's team)"),
            M(sftp, "- Sep 18: Secure file transfer (SFTP) access ready (Sandra)"),
            M(tax, "- Oct 1: State and local tax configuration complete (Tomás)"),
            U(_find(prompt, "commitment", "implementation plan"), status="done", quote="As promised, the implementation plan is attached."),
            M(_find(prompt, "commitment", "timeclock"), "We still need to find out whether they offer an API."),
            M(_find(prompt, "decision", "Biweekly"), "Pay frequency (biweekly, Friday pay dates) and overtime rules (Ohio 8-and-80, Pennsylvania weekly 40) are locked in per the kickoff."),
            C(item_type="commitment", summary="Marcus Lee: run the parallel payroll cycle", owner="Marcus", due_date="2026-10-26",
              quote="- Week of Oct 26: Parallel payroll cycle (Marcus)"),
        ]
    if "Title: #riverbend-implementation (Sep 18" in prompt:
        return [
            U(_find(prompt, "commitment", "SFTP"), status="done", quote="SFTP access is live."),
            U(_find(prompt, "commitment", "employee data file"), due_date="2026-10-06", quote="I should have it by Oct 6."),
            C(item_type="risk", severity="high", summary="Employee data file is late (duplicate records), putting go-live at risk",
              quote="Heads up, the employee data file is going to be late."),
            M(_find(prompt, "commitment", "timeclock"), "I still don't know who on the ADP side owns it."),
            C(item_type="risk", severity="low", summary="Real employee data (SSN, DOB, salary) was pasted into a chat channel",
              quote="Please don't paste real employee data in chat though, use SFTP for anything like that."),
            C(item_type="risk", severity="high", summary="Timeclock API still unconfirmed after repeated asks; may mean manual time imports",
              quote="Bumping the timeclock question again. That's the third time I'm asking."),
        ]
    if "Title: Weekly Status Meeting (Oct 1)" in prompt:
        return [
            U(_find(prompt, "commitment", "employee data file"), due_date="2026-10-08", quote="We have it due October 8, and that gates Tomás's tax configuration."),
            C(item_type="risk", severity="medium", summary="Conflicting data file due dates: Oct 6 in Slack vs Oct 8 in the meeting",
              quote="We have it due October 8, and that gates Tomás's tax configuration."),
            U(_find(prompt, "commitment", "tax configuration"), due_date="2026-10-09", quote="If it arrives October 8, I can finish the tax configuration by October 9."),
            M(_find(prompt, "commitment", "parallel"), "I'd like us to run one full parallel payroll cycle the week of October 26, before go-live"),
            C(item_type="commitment", summary="Greg Hallman: review parallel run results and sign off", owner="Greg", due_date="2026-10-30",
              quote="I'll review the comparison and sign off by October 30."),
            M(_find(prompt, "decision", "parallel"), "Decision made, then. One parallel cycle the week of October 26."),
            M(_find(prompt, "commitment", "timeclock"), "I raised it at kickoff and again in Slack yesterday and haven't heard anything."),
        ]
    if "Title: Weekly Status Meeting (Oct 8)" in prompt:
        return [
            U(_find(prompt, "commitment", "employee data file"), due_date="2026-10-16", quote="I can get it to you by Friday, October 16."),
            M(_find(prompt, "risk", "late"), "IT is still fixing duplicates and missing tax withholding fields in the export."),
            C(item_type="commitment", summary="Priya Raman: confirm final Pennsylvania headcount", owner="Priya", due_date="2026-10-14",
              quote="I'll get you the confirmed number by October 14."),
            C(item_type="opportunity", summary="About 80 new Pennsylvania employees expected in January",
              quote="we're also planning to add about 80 employees at the Pennsylvania clinics in January."),
            C(item_type="risk", severity="medium", summary="Pennsylvania headcount unconfirmed; a change over 10% needs a change order",
              quote="A headcount change of more than ten percent needs a change order under the statement of work, so we should look at that once the number is final."),
            C(item_type="commitment", summary="Dana Whitfield: send revised project plan with the new data file date", owner="Dana", due_date="2026-10-09",
              quote="I'll send a revised project plan reflecting the new data file date by tomorrow, October 9."),
            M(_find(prompt, "commitment", "timeclock"), "Same question as before about the timeclock vendor."),
            C(item_type="risk", severity="medium", summary="Tax configuration due Oct 9 can't finish before the data file arrives Oct 16",
              quote="If the file shows up, yes."),
        ]
    if "Title: #riverbend-implementation (Oct 9" in prompt:
        return [
            U(_find(prompt, "commitment", "revised project plan"), status="done", quote="Revised project plan is in the shared folder."),
            C(item_type="risk", severity="medium", summary="Client believes go-live is Nov 9, not Nov 2",
              quote="Huh. I had understood it was Nov 9."),
            C(item_type="risk", severity="high", summary="Client payroll manager is losing confidence and may escalate to their CFO",
              quote="Honestly I'm losing confidence we'll be ready on any date."),
            M(_find(prompt, "commitment", "timeclock"), "Still nothing on the timeclock API."),
            M(_find(prompt, "commitment", "Pennsylvania headcount"), "Final Pennsylvania headcount: finance hasn't signed off yet."),
            M(_find(prompt, "commitment", "employee data file"), "Is the data file still on track for Friday?"),
        ]
    return []


class RichProvider:
    """Dispatches on the system prompt, like the real model would see three different jobs."""
    name = "scripted-rich"

    def __init__(self):
        self.calls = 0
        self.last_user = None

    def generate(self, system, user, schema, model):
        self.calls += 1
        self.last_user = user
        if system.startswith("You maintain a client's implementation memory"):
            return json.dumps({"operations": extraction_ops(user)})
        if system.startswith("You answer questions"):
            doc = re.search(r"^doc (\d+): Weekly Status Meeting \(Oct 8\)", user, re.M)
            data_id = _find(user, "commitment", "employee data file")
            return json.dumps({
                "answer": f"The employee data file moved from Sep 22 to Oct 6 to Oct 8 to Oct 16 [#{data_id}] and is overdue. See also [#9999].",
                "citations": [{"doc_id": int(doc.group(1)), "quote": "I can get it to you by Friday, October 16."},
                              {"doc_id": int(doc.group(1)), "quote": "Priya promised it would arrive on Tuesday."}],  # made up: must be dropped
            })
        if system.startswith("You write a HANDOFF BRIEF"):
            data_id = _find(user, "commitment", "employee data file")
            return json.dumps({"summary": "At risk but recoverable. The data file is the blocker.",
                               "sections": [{"title": "At risk", "bullets": [
                                   {"text": "The data file is late.", "item_ids": [data_id, 9999]},
                                   {"text": "", "item_ids": []}]}, {"title": "Empty", "bullets": []}]})
        if system.startswith("You pick the NEXT BEST ACTIONS"):
            data_id = _find(user, "commitment", "employee data file")
            return json.dumps({"actions": [{"action": "Call Priya about the data file.", "why": "It is overdue.",
                                            "item_ids": [data_id, 9999], "urgency": "today"}]})
        raise AssertionError("unexpected prompt: " + system[:60])
