"""Offline test of elm/entry.py: real titles seen on company boards, with the label a new graduate would expect."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from elm import entry  # noqa: E402

I, E, N = "intern", "entry", None
TITLES = [
    # internships
    ("Risk Advisory Intern", I), ("Summer 2027 MBA Internship – Strategy and Operations", I), ("2027 MBA Intern – Product Manager", I),
    ("NIKE, Inc. Supply Chain Planning Graduate Internship", I), ("Spring Co-Op 2027, Predictive Models", I), ("Legal Intern", I),
    ("2027 Summer Intern, PhD, Planner Machine Learning", I), ("Store Executive Intern (Store Leadership Intern)", I),
    ("Summer Associate, Strategy", I), ("Tax Operations Intern", I),
    # entry: the new-grad forms
    ("Software Engineer, New Grad (Dec 2026)", E), ("2027 University Graduate - Enterprise Architecture Analyst", E),
    ("Software Engineer, Early Career (NYC)", E), ("Entry Level Application Engineer - 2027 Start Date", E),
    ("Rotational Development Program", E), ("Leadership Development Program 2027", E), ("Sales Leadership Development Program", E),
    ("Associate Product Manager, New Grad (2027 Start)", E), ("Associate Program Manager", E), ("Sales Development Representative - New Grad", E),
    ("Business Development Representative", E), ("Process Engineer I, New College Grad - Bachelors", E), ("Junior Financial Analyst", E),
    ("Management Development Program", E), ("Technology Leadership Program – Data Analyst - PA", E),
    # entry: plain analyst / associate in professional functions
    ("Financial Analyst, FP&A", E), ("Business Operations Associate", E), ("Strategic Finance Analyst", E), ("Marketing Associate", E),
    ("Analyst, Firm Strategy and Execution", E), ("Operations Associate", E),
    # traps: hourly, warehouse, part time, seasonal, contract
    ("Seasonal Full Time Hourly Warehouse Associate Openings (T3863)", N), ("Part-Time Client Service Associate", N),
    ("Retail Associate - Nike (COCO)", N), ("Retail Assistant (Athlete) TEMP PT8HRS", N), ("Associate Medical Coordinator (Direct Hire Contractor)", N),
    ("Field Service Technician (Entry Level) - Dallas", N), ("Entry Level Manufacturing Technician", N), ("Clinical Associate", N),
    ("Registered Client Service Associate", N), ("Lab Analyst", N),
    # traps: finance Associate is not entry (unless the title says analyst program / new grad)
    ("Associate, Transaction Support Group", N), ("Associate, Private Equity", N), ("Investment Banking Associate", N),
    ("Credit Associate, Leveraged Finance", N), ("Wealth Management Associate", N), ("Associate, Healthcare", N),
    ("Investment Banking Analyst Program, New Grad", E),
    # senior
    ("Senior Financial Analyst", N), ("Analyst II, Finance", N), ("Product Manager", N), ("Director of Operations", N), ("Lead Data Analyst", N),
    ("Principal Software Engineer", N), ("Associate Director, Strategy", N), ("Military SkillBridge – Data Analyst", N),
    # no signal at all
    ("Account Executive", N), ("Software Engineer", N), ("Product Designer", N), ("Warehouse Associate", N),
]
for title, want in TITLES:
    got = entry.label(title)
    assert got == want, f"label({title!r}) = {got!r}, want {want!r}"
assert len(TITLES) >= 40

# flags
assert "mba_only" in entry.flags("Adobe 2027 MBA Intern – Product Manager", "")
assert "phd_only" in entry.flags("2027 Summer Intern, PhD, Planner", "")
assert "part_time" in entry.flags("Part-Time Client Service Associate", "")
assert "hourly" in entry.flags("Warehouse Associate", "") and "seasonal" in entry.flags("Seasonal Associate", "")
assert "contract" in entry.flags("Analyst (Contract)", "")
assert "mba_only" in entry.flags("Strategy Intern", "You must be currently pursuing an MBA at an accredited school.")
assert entry.flags("Financial Analyst", "A great team.") == []

# classify: years demote entry, never promote; hourly text drops entry but not an internship
assert entry.classify("Financial Analyst", "We need 5 years of experience in finance.") == (None, [])
assert entry.classify("Financial Analyst", "At least 1 year of experience is a plus.")[0] == "entry"
assert entry.classify("Senior Financial Analyst", "0 years of experience needed")[0] is None
assert entry.classify("Operations Analyst", "This is a non-exempt, paid hourly position.")[0] is None
lab, fl = entry.classify("Marketing Intern", "Pay: $30 per hour. You have 5 years of experience.")
assert lab == "intern" and "hourly" not in fl
assert entry.min_years("Requires 3+ years of relevant experience") == 3 and entry.min_years("no mention") is None

# location
us = entry.location_us
assert us("New York, NY") is True and us("United States - Remote") is True and us("Santa Clara,CA") is True
assert us("London, United Kingdom") is False and us("Bangalore, Karnataka, India") is False and us("SINGAPORE TC-BIOPOLIS") is False
assert us("Vladivostok") is False and us("Kulim,MYS") is False and us("Toronto, ON, CA") is False
assert us("Albuquerque, New Mexico") is True and us("Remote") is None and us("3 Locations") is None and us(None) is None
assert us("Hybrid", "Business Development Representative - German Speaking") is False
print("elm entry: ALL OK")
