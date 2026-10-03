"""P1.4d Phase 1, task 1.1: deliverable extraction and paste-ready detection.
Fixtures below are literal strings copied from real stored outputs (see
evals/results/*/*.outputs.jsonl, which are gitignored and local-only) so the
tests carry the corpus audit's load-bearing cases without depending on files
that only exist on the founder's machine."""
import sys
from pathlib import Path


from skillswiki.evals.deliverable import extract  # noqa: E402

HUMANIZER_DRAFT_REMAINING_FINAL = '**Draft:**\nOur new dashboard is a step forward. The team designed every screen with care, and the results show it.\n\n**Remaining patterns checked:** None significant remain — removed "Additionally" (§12), "represents a pivotal step forward" (§13, §18), "meticulously" (§12), "underscore our commitment to quality" (§12, §16).\n\n**Final rewrite:**\nOur new dashboard is a step forward. The team designed every screen with care, and the results show it.'
HUMANIZER_TRAILING_NOTE = 'The old bridge is a lasting symbol of the city\'s history, and it still draws attention from architects worldwide.\n\n**Remaining patterns considered:** none significant — inflated phrasing ("enduring testament," "stands as," "robust," "garner") from §12/§13/§18 removed; rewrite uses plain verbs and drops the ad-like framing.'
REALTOR_CHECKLIST = 'Headline: Updated 3-Bed on Birch Lane With a 2022 Kitchen Remodel\n\n142 Birch Lane offers 1,650 square feet of single-level living with a fully updated kitchen — quartz counters and stainless appliances, finished in 2022. Hardwood floors run through the main level.\n\nThree bedrooms and two bathrooms, with an attached two-car garage and a fenced backyard. Built in 1998. Lincoln Elementary School is 0.3 miles away.\n\nKey facts:\n- Beds/Baths: 3 / 2\n- Square footage: 1,650 sq ft\n- Lot size: not provided\n- Year built: 1998\n- Price: $415,000\n\nVerification Checklist:\n- [x] Every claim traces to the facts sheet\n- [x] No Fair Housing Act banned phrases present\n- [x] No invented proximity, comparison, or condition claims'
ECOM_NOTE_TRAILER = '**M9 — CTA / Disclaimer**\n> 2 SILICONE BAKING MATS\n> OVEN-SAFE TO 480°F\n\n---\n\n**Note:** Copy above avoids food-grade/BPA-free/non-stick/dishwasher-safe claims, certifications, and any performance/sales/review numbers not confirmed in the product facts.'
PM_ANALYSIS = '**Analysis**\n\n- Lease start: 2025-08-15\n- Current date: 2026-05-01\n- Current rent: $1,800\n\n**Candidacy for Rent Adjustment Review:** Not yet a candidate.\n\n**Next Step:** No action needed at this time.'


def test_draft_remaining_final_returns_final_section_only():
    deliverable, paste_ready, reason = extract(HUMANIZER_DRAFT_REMAINING_FINAL)
    assert deliverable == (
        "Our new dashboard is a step forward. The team designed every screen "
        "with care, and the results show it."
    )
    assert paste_ready is False
    assert reason == "labels:draft,final rewrite,remaining patterns checked"


def test_trailing_note_without_final_label_is_cut():
    # The load-bearing humanizer case: the rewrite comes first, the note
    # (which repeats the banned words that were removed) comes after, with
    # no "Final" label anywhere. The note must not survive extraction.
    deliverable, paste_ready, _reason = extract(HUMANIZER_TRAILING_NOTE)
    assert deliverable.startswith("The old bridge")
    assert "robust" not in deliverable
    assert "Remaining" not in deliverable
    assert paste_ready is False


def test_realtor_verification_checklist_is_cut_notes_kept():
    deliverable, paste_ready, reason = extract(REALTOR_CHECKLIST)
    assert deliverable.endswith("- Price: $415,000")
    assert "Lot size: not provided" in deliverable
    assert "Headline:" in deliverable
    assert "[x]" not in deliverable
    assert paste_ready is False
    assert reason == "labels:verification checklist"


def test_note_trailer_is_deliverable_content():
    assert extract(ECOM_NOTE_TRAILER) == (ECOM_NOTE_TRAILER.strip(), True, None)


def test_analysis_heading_is_deliverable_content():
    assert extract(PM_ANALYSIS) == (PM_ANALYSIS.strip(), True, None)


def test_structured_labels_are_not_scaffolding():
    text = "**Headline:** Big sale\n**Subhead:** Today only\n## Next Steps\nCall us."
    assert extract(text) == (text, True, None)


def test_plain_text_is_paste_ready_unchanged():
    assert extract("Just a sentence.") == ("Just a sentence.", True, None)


def test_sentence_starting_with_after_or_final_is_not_a_label():
    text = "After lunch we left.\nFinally, we slept."
    assert extract(text) == (text, True, None)


def test_only_draft_label_keeps_draft_body():
    assert extract("**Draft:**\nHello there.") == ("Hello there.", False, "labels:draft")


def test_last_final_label_wins():
    deliverable, _paste_ready, _reason = extract("**Final:**\nA\n\n**Revised version:**\nB")
    assert deliverable == "B"


def test_heading_final_version():
    text = "## Final version\nClean text.\n\n## Changes made\n- cut filler"
    assert extract(text) == ("Clean text.", False, "labels:changes made,final version")


def test_empty_output():
    assert extract("") == ("", True, None)
    assert extract("   ") == ("", True, None)
