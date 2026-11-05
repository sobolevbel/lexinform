# Senate identity text-input bridge

The existing verified Senate read now retains its actual loaded file and source-read capture
locally. After unchanged identity and budget checks, the optional input receipt reuses that exact
captured selection. The raw file hash, extracted text hash, character bounds and complete selection
identity must agree. Only a complete identity text is mapped; the bridge does not infer offsets
for a removed cover prefix, substitute a native selection or perform another read.

The shared amendment receipt profile handles persistence. Scans, resumed contexts and prefix
variants remain unstamped. Optional metadata errors preserve preparation; cancellation and
original source-validation failures still propagate. There is no change to selection, admission,
text transformations, provider payload or storage backend.

The existing corpus replay covers 1,825 files, 816 observations and 4,903 mutation probes with
unchanged decisions. Root independently repeats native public preparation for all 701 selected
text inputs, totaling 39,243,213 raw bytes, against the frozen pre-code baseline. Every request,
context, source observation, ordered download and preparation count remains identical. The 636
identity cases gain receipts with independently checked raw artifact, extraction, selection and
source segment parents. All 65 prefix cases remain unmapped. The 23 scan-pending cases, two cases
without a unique date and 90 non-amendment cases are outside this input profile.

Prior analysis context is controlled fixture data, and providers are disabled. This is prospective
input provenance qualification, not reconstruction of historical paid intents or legal-role gold.
Eleven new unit cases cover actual/cache reads, recommendation-last, resumed/prefix/scan exclusions,
corrupt source/text bounds, optional metadata failure and cancellation. No scenario is added.

[Qualification](qualification.json), [corpus proof](corpus-proof.json),
[frozen native baseline](native-baseline.json.gz), [root paired replay](native-paired.json.gz),
[stage replay](stage-replay.json), [state proof](state-proof.json),
[source pins](source-pins.json), [independent review](peer-review.json).

Previous release: [exact CI and relay verification](previous-release.json).

Root full gate: 8,354 tests in 289.60 seconds, 92% coverage, strict mypy over 730 files,
Ruff and formatting green; all 799 source/config pins remain exact. The pinned state copy retains
all 508 bill and 201 status-change public serializations, full default doctor output and double
restore; its database hash remains unchanged. DA5 and the full lifecycle acceptance remain open.

Actual Senate bridge release: [exact CI and relay verification](release.json), commit7b196312.
