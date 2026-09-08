# How a Polish law is made (and where the public can act)

A reference for working on lexinform: the whole path of a bill (projekt ustawy) from the first
public trace to entry into force, the legal deadlines on that path, the moments when organisations
and individuals can submit an opinion, and how each step shows up in the Sejm API and in our
models. Sources: Constitution of 2 April 1997 (arts. 118–123, 235), Regulamin Sejmu, Regulamin
Senatu, Regulamin pracy Rady Ministrów (uchwała nr 190 of 2013), ustawa o ogłaszaniu aktów
normatywnych, ustawa o działalności lobbingowej, ustawa o petycjach; API facts verified live in
September 2026 (see `roadmap.md`). Paragraph numbers are given only where we are sure of them.

## 0. The map

```
                 GOVERNMENT BILLS                          OTHER BILLS (deputies, President,
                 (Rada Ministrów)                          Senate, Sejm committee, citizens)
                 ────────────────                          ─────────────────────────────────
  wykaz prac legislacyjnych RM (KPRM)  UD/UC number
  ↓
  RCL: uzgodnienia · KONSULTACJE PUBLICZNE (≥ 21 d) · opiniowanie · raport z konsultacji
  ↓  komitety RM → Komisja Prawnicza → Rada Ministrów adopts
  ↓
  ═══════════════════════════ submitted to Marszałek Sejmu ═══════════════════════════
  RPW/…/yyyy number in /bills                              RPW number; Marszałek may order
  (no Sejm consultation: done on RCL)                      SEJM CONSULTATIONS (30 d, web form)
  ↓
  druk number → /processes                                 ← lexinform's main entry point
  I czytanie (committee, or plenary for the big topics; ≥ 7 d after the print reaches MPs)
  committee work: podkomisja · wysłuchanie publiczne (optional) · opinions · sprawozdanie
  II czytanie (plenary; amendments → back to committee → sprawozdanie "-A")
  III czytanie: vote (zwykła większość, quorum ½) → closureDate, passed=true
  ↓
  Senat: ≤ 30 d (14 d if pilny): accept / amend / reject → Sejm may override (absolute majority)
  ↓
  Prezydent: ≤ 21 d (7 d if pilny): sign / veto (Sejm overrides by 3/5) / refer to Trybunał
  ↓
  Dziennik Ustaw (ELI DU/yyyy/pos) → vacatio legis (default 14 d) → in force
  ↓
  rozporządzenia (ministers, RM) that make the act operative: RCL again, no Sejm
```

Only one of the two consultation windows exists for a given bill, and which one depends on the
author. That is why 541 of the 1279 entries in `/bills` of the 10th term (the government bills)
have no Sejm consultation: theirs happened on RCL, months earlier.

## 1. Who may propose a bill (Constitution art. 118)

- **Council of Ministers** (rządowy projekt): the bulk of substantive legislation, incl. the
  aliens act, the Ukraine special act, labour-market and social-security changes, EU
  implementation. Comes with a full OSR and a consultation report.
- **Deputies** (poselski): at least 15 MPs or a Sejm committee (komisyjny). Fast to table, often
  thin justification, sometimes a vehicle for government ideas to skip RCL (then the bill has no
  public consultation at all until the Sejm one).
- **Senate** (senacki), **President** (prezydencki), **citizens** (obywatelski: 100 000
  signatures; the only bills that survive the end of a term, see §9).

`applicantType` in `/bills` gives this explicitly; for `/processes` we derive it from the title
prefix ("Rządowy projekt…", "Poselski projekt…"). The bill must carry an uzasadnienie explaining
the need, the effects (social, economic, financial, legal), the sources of funding, EU conformity
and the results of consultations already held (Regulamin Sejmu art. 34).

## 2. Government bills before the Sejm (RCL)

The government path is regulated by the Regulamin pracy Rady Ministrów and is public on
`legislacja.rcl.gov.pl` (Rządowy Proces Legislacyjny, RPL). Typical duration: 3–12 months, longer
for large codifications.

1. **Wykaz prac legislacyjnych i programowych Rady Ministrów** (kept by KPRM, published on
   gov.pl). A bill gets a number: `UD` (ustawa), `UC` (ustawa implementing EU law), `RD`
   (rozporządzenie RM). The entry names the ministry, the planned adoption quarter and the essence
   of the change: this is the earliest public trace of a future law, often a year ahead.
2. **Projekt** published on RCL (`/projekt/{id}`): text, uzasadnienie, OSR (ocena skutków
   regulacji, the 13-point form), for UC bills the tabela zgodności.
3. **Uzgodnienia** (inter-ministerial), **opiniowanie** (bodies whose opinion the law requires:
   trade unions and employer organisations via Rada Dialogu Społecznego, Komisja Wspólna Rządu i
   Samorządu Terytorialnego, KRS, Sąd Najwyższy, GIODO/UODO, NBP, RCL itself…) and
   **konsultacje publiczne** run in parallel. The consultation letter (pismo kierujące do
   konsultacji publicznych) names the deadline and the address for comments; RCL's procedure
   description gives 21 days as the minimum for a bill, shorter terms need a justification.
   Anyone may comment: organisations and individuals alike, in Polish, by e-mail/ePUAP to the
   ministry. There is no form on RCL.
4. **Raport z konsultacji**: the ministry lists the comments and says what it accepted and why
   not. Separately, anyone may file a **zgłoszenie zainteresowania pracami nad projektem** under
   the lobbying act; these are published on the project page ("zgłoszenia lobbingowe").
5. **Committees of the Council of Ministers**: KRMC (digital), KSRM (social), KERM (economic),
   then the **Stały Komitet Rady Ministrów**; **Komisja Prawnicza** (RCL lawyers, drafting
   quality); for technical regulations an EU notification; adoption by the **Rada Ministrów**.
6. The Prime Minister sends the bill to the Marszałek Sejmu. From here it is a Sejm process.

Data: RCL has no API or RSS (both answer "Request Rejected"); the HTML list
(`/lista?typeId=2`, params `pNumber`, `pSize`) and project pages are readable. `/processes` gives
`rclNum` and `rclLink`, so a Sejm print can be joined to its RCL project after the fact.
Regulations (rozporządzenia) follow the same RCL path and never reach the Sejm (§8).

## 3. Submission to the Sejm and the print number

Every bill is submitted to the Marszałek Sejmu and gets an RPW number (`RPW/29075/2026`), visible
in `/bills` with `dateOfReceipt`, `status` (ACTIVE, WITHDRAWN, NOT_PROCEEDED, OBSOLETE, ADOPTED),
`submissionType` (BILL, DRAFT_RESOLUTION, …), `print` once assigned.

Before assigning a print number the Marszałek:

- checks the formal requirements (art. 34); a bill that does not meet them can be returned;
- may ask for opinions (Biuro Analiz Sejmowych, Biuro Legislacyjne, the Komisja Ustawodawcza on
  constitutionality; bills that raise doubts may be referred to it);
- for deputies', President's, Senate, committee and citizens' bills may order **public
  consultation on the Sejm website**: `publicConsultation`, `publicConsultationStart/EndDate`
  (30 days in 299 of 318 observed cases), then `consultationResults` when the opinions received
  are published. The form and the opinions live at
  `www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTOWANY_PROJEKT&NrProjektu=<RPW number>`
  (browser only). Government bills skip this: they were consulted on RCL.

Assigning the **druk number** creates the `/processes/{number}` entry (stage `Start`, "Projekt
wpłynął do Sejmu", carries `printNumber`). Everything before that is invisible in `/processes`,
which is why lexinform also reads `/bills`. Pre-print bills carry only a title and an official
description; the PDF sits on orka.sejm.gov.pl behind a bot wall.

## 4. Three readings in the Sejm (Constitution art. 119, Regulamin Sejmu)

**Referral to the first reading** (`ReadingReferral` + child `Referral` with `committeeCode`).
The first reading takes place either in a committee (the usual case; several committees possible)
or at a plenary sitting (`committeeCode: "Sejm"`), which is mandatory for constitutional
amendments, the budget, taxes, electoral law, codes, local government and civil rights and
liberties (art. 37). It may not be held earlier than 7 days after the print reached the MPs
(art. 37 ust. 4) unless the Sejm decides otherwise.

**I czytanie** (`Reading` "I czytanie w komisjach", or `SejmReading` "I czytanie na posiedzeniu
Sejmu" with `sittingNum`): the applicant's representative presents the bill, debate, questions.
At a plenary first reading the Sejm either refers the bill to committees or **rejects it
outright** (odrzucenie w pierwszym czytaniu: the process ends, `passed=false`, `closureDate` set).

**Committee work** (`CommitteeWork` "Praca w komisjach po I czytaniu"). The committee (often a
podkomisja first) goes through the text article by article, hears the government, experts, BAS
and the Biuro Legislacyjne. Opinions from outside are accepted: organisations and individuals
write to the committee secretariat (the committee page lists it), and under the lobbying act a
formal zgłoszenie zainteresowania may be filed. The committee may hold a **wysłuchanie publiczne**
(`PublicHearing`): announced at least 14 days ahead, participation requires an application at
least 10 days before the hearing (Regulamin Sejmu art. 70a–70i); everyone who applied speaks.
The result is the **sprawozdanie komisji** (`CommitteeReport` with `printNumber`, `reportFile`,
`proposal`): "załączony projekt ustawy" (a consolidated text with the committee's amendments; the
document we re-analyse), "przyjąć bez poprawek", or "odrzucić". Minority motions (wnioski
mniejszości) are attached. This is where most substantive changes to a bill happen.

**II czytanie** (`SejmReading` "II czytanie na posiedzeniu Sejmu"): the report is presented,
debate, and **amendments** may be tabled (by 15 MPs, the committee, the applicant, the Council of
Ministers). If there are amendments the bill goes back to the committee
(`decision: "skierowano ponownie do komisji…"`), which issues an **additional report**: the
`-A` print (`proposal: "przyjąć poprawki"` / "odrzucić poprawki"): a table of amendments, not a
text. If there are none, the Sejm usually moves straight on (`decision: "niezwłocznie przystąpiono
do III czytania"`).

**III czytanie** (`SejmReading` "III czytanie…", `decision: "uchwalono"` / "odrzucono", child
`Voting` with totals; per-club votes via `/votings/{sitting}/{n}`): votes in this order: motion to
reject the whole bill, amendments (grouped), the bill as a whole. Ordinary laws pass by a **simple
majority with at least half of the MPs present** (art. 120); some decisions need an absolute or
qualified majority. `closureDate` is the date of the third reading and `passed=true` means
"adopted by the Sejm", not "in force". The text after the third reading is published as
`textAfter3` (also re-analysed by us). The Marszałek sends the ustawa to the Senate.

**Urgency** (art. 123, `urgencyStatus`): the Council of Ministers may declare its bill *pilny*
(not for taxes, elections, the constitution, codes, and a few more). Then the Senate has 14 days
and the President 7.

**Joint consideration** (`printsConsideredJointly`): several bills on the same matter are handled
together; the committee report merges them, so a bill we follow may continue under another number.

## 5. The Senate (Constitution art. 121)

The Senate has **30 days** from receipt (14 if pilny) to adopt the law unchanged, adopt
amendments, or reject it as a whole; silence means adoption. Senate committees work on the text
and accept opinions too (the Senate publishes its own consultation pages per print). The Senate's
resolution comes back to the Sejm as a Sejm print (`SenatePosition` with `position`: "nie wniósł
poprawek", "wniósł poprawki"/"wniósł poprawkę", "odrzucił ustawę", and `printNumber`).

Amendments or a rejection are considered by the Sejm committee (`CommitteeWork` "Praca w komisjach
nad stanowiskiem Senatu" + `CommitteeReport` "przyjąć/odrzucić (część) poprawek") and voted on
(`SenatePositionConsideration`, `decision`: "przyjęto poprawki", "przyjęto część poprawek",
"odrzucono poprawki"). A Senate amendment or rejection stands unless the Sejm rejects it by an
**absolute majority** with at least half of the MPs present. If the Sejm cannot override a
rejection, the law dies. The Senate's own text is not analysed by lexinform yet (roadmap).

## 6. The President (Constitution art. 122)

The Marszałek Sejmu sends the adopted law to the President (`ToPresident`), who has **21 days**
(7 if pilny or budget) to:

- **sign** it and order publication in Dziennik Ustaw (`PresidentSignature`, then `End`);
- **refer it to the Trybunał Konstytucyjny** before signing (`PresidentToTribunal`); if the
  Tribunal finds it constitutional the President must sign; if parts are unconstitutional and
  not inseparable, the President signs without them or returns the law to the Sejm to fix them;
- **veto** it (`Veto`; formally "wniosek o ponowne rozpatrzenie", with reasons). The Sejm
  overrides with a **3/5 majority** in the presence of at least half of the MPs; then the
  President must sign within 7 days and may no longer go to the Tribunal. Vetoed laws keep
  `passed=true` in the API: `passed` is not "in force".

## 7. Publication and entry into force

Publication in **Dziennik Ustaw** (Dz.U., published by RCL) is the signal we watch: `/processes`
gets `ELI` (`DU/2026/1099`), `address`, `displayAddress` ("Dz.U. 2026 poz. 1099") and ISAP/ELI
links; `/eli/acts/DU/{year}/{pos}` gives `promulgation` (the Dz.U. date), `entryIntoForce`,
`announcementDate` (the date in the act's title, i.e. the third-reading date), `status`, texts.
Observed: publication ~30–40 days after the Sejm vote (Senate + President + printing).

**Vacatio legis** (ustawa o ogłaszaniu aktów normatywnych): an act enters into force **14 days
after publication** unless it says otherwise (art. 4). Longer periods are common for laws that
require preparation; "z dniem ogłoszenia" or "z dniem następującym po dniu ogłoszenia" is
allowed only when an important state interest demands it and constitutional principles do not
preclude it. **Staged entry** (different articles on different dates, sometimes a year apart) is
very common in the acts we follow; the ELI API exposes one `entryIntoForce`, so our in-force
reminder always carries the note that some provisions may start later. Consolidated texts
(tekst jednolity) are later re-published as obwieszczenia in Dz.U.; they change nothing.

## 8. After the act: regulations and other instruments

Many rules that matter to a foreigner in practice are not in the act but in **rozporządzenia**
(fees, application forms, deadlines for offices, lists of documents, the wzór karty pobytu). They
are issued by ministers or the Council of Ministers on the basis of a delegation in the act, go
through RCL (uzgodnienia, konsultacje publiczne, opiniowanie, Komisja Prawnicza) and are
published in Dz.U.; the Sejm is not involved. Their entry into force is often aligned with the
act's. lexinform does not cover them (roadmap: RCL).

Other instruments that are not laws but affect readers: **obwieszczenia** (announcements, e.g.
thresholds indexed yearly), **uchwały Rady Ministrów** (programmes, not binding on citizens), EU
regulations applying directly (no Polish act needed). None of these are in `/processes`.

## 9. Term of the Sejm and discontinuation

A Sejm term (kadencja) lasts four years; print numbers restart at 1 with every term, which is
why our bill tag is `#kadencja10druk3039`. **Zasada dyskontynuacji**: bills not finished by the
end of the term lapse and must be re-submitted; the exception by statute is the citizens' bill,
which is taken over by the next Sejm. `/bills` marks lapsed entries as NOT_PROCEEDED/OBSOLETE.

## 10. Typical timeline

| Step | Legal limit | Observed |
|---|---|---|
| Wykaz prac RM → RCL project | none | weeks to months |
| RCL consultations | ≥ 21 days for a bill (shorter with justification) | 14–30 days, sometimes 7 |
| RCL total (project → Sejm) | none | 3–12 months |
| Sejm consultation (RPW) | set by the Marszałek | 30 days |
| RPW → druk number | none | days to months (some bills wait in a "freezer") |
| Print → first reading | ≥ 7 days after delivery to MPs | weeks to months |
| Committee work | none | weeks to a year; urgent bills: days |
| II + III czytanie | none | often the same sitting |
| Senate | 30 days (14 pilny) | 1–4 weeks |
| President | 21 days (7 pilny) | 1–3 weeks |
| Dz.U. publication | "niezwłocznie" | days after signature; ~30–40 days after the Sejm vote |
| Vacatio legis | default 14 days | 14 days to 12+ months, staged |

A government bill that matters to foreigners therefore typically has: one RCL consultation
window (months before it is visible to us), none in the Sejm, committee work where opinions can
still be sent, then 2–3 months of Senate/President/publication before anything changes in
practice.

## 11. Where the public can act (and how lexinform maps it)

| Moment | Who may act | How | In lexinform |
|---|---|---|---|
| RCL konsultacje publiczne (government bills, regulations) | anyone, in Polish | e-mail/ePUAP to the ministry, address in the consultation letter; lobbying declaration | not covered (roadmap) |
| Sejm consultation of an RPW bill (non-government) | anyone | web form on sejm.gov.pl, 30 days | consultation line + link, reminder 3 days before, notice when opinions are published |
| Committee work after the first reading | anyone; organisations formally via lobbying declaration | letter/e-mail to the committee secretariat, ideally before the sitting that handles the bill | "what you can do now" with the committee link; committee sitting agenda posts |
| Wysłuchanie publiczne | anyone who applies ≥ 10 days before | application via the Sejm's system | `PublicHearing` label + action line |
| Senate committee stage | anyone | opinion to the Senate committee | not covered (only the outcome) |
| Petition (any time, incl. after the act) | anyone (ustawa o petycjach), no citizenship requirement | petition to the Sejm (Komisja do Spraw Petycji), Senate or a ministry | not covered |
| After entry into force | — | compliance; a new bill is needed to change it | in-force reminder |

## 12. Sejm API stage vocabulary (as seen in the fixtures)

Top-level stages in `/processes/{n}.stages`, children indented:

```
Start                        Projekt wpłynął do Sejmu                  printNumber
ReadingReferral              Skierowano do I czytania w komisjach / na posiedzeniu Sejmu
  Referral                   Skierowanie                                committeeCode ("Sejm" = plenary)
Reading                      I czytanie w komisjach
SejmReading                  I/II/III czytanie na posiedzeniu Sejmu    sittingNum, decision
  Referral                   (after a plenary reading or "skierowano ponownie")
  Voting                     Głosowanie (III czytanie)                 voting{yes,no,abstain,…}
CommitteeWork                Praca w komisjach po I/II czytaniu / nad stanowiskiem Senatu
  CommitteeReport            Sprawozdanie komisji                      printNumber, reportFile, proposal
PublicHearing                Wysłuchanie publiczne                     date
SenatePosition               Stanowisko Senatu                         position, printNumber
SenatePositionConsideration  Rozpatrywanie na forum Sejmu stanowiska Senatu   decision
ToPresident                  Ustawę przekazano Prezydentowi do podpisu
PresidentSignature           Prezydent podpisał ustawę
Veto / PresidentToTribunal
End                          Uchwalono
```

`models.next_phase` turns the last top-level stage into the reader-facing "what comes next";
`Stage.carries_bill_text` decides which committee report is a text worth re-analysing (`-A`
reports and "przyjąć poprawki" are amendment tables). `passed` = adopted by the Sejm; `ELI` =
published; `/eli/acts` `entryIntoForce` = in force. Timestamps in the API are naive Warsaw time.

## 13. Glossary (PL → RU / EN)

| Polish | Russian | English |
|---|---|---|
| projekt ustawy | законопроект | bill |
| druk (sejmowy) | сеймовый документ с номером | Sejm print |
| poselski / rządowy / senacki / prezydencki / obywatelski / komisyjny | депутатский / правительственный / сенатский / президентский / гражданский / комиссионный | deputies' / government / Senate / President's / citizens' / committee bill |
| uzasadnienie | обоснование | explanatory memorandum |
| OSR (ocena skutków regulacji) | оценка последствий регулирования | regulatory impact assessment |
| konsultacje publiczne (RCL) / konsultacje społeczne (Sejm) | общественные консультации | public consultation |
| uzgodnienia / opiniowanie | межведомственное согласование / получение обязательных мнений | inter-ministerial review / statutory opinions |
| czytanie (I, II, III) | чтение | reading |
| skierowanie do komisji | направление в комиссию | referral to committee |
| sprawozdanie komisji | отчёт (заключение) комиссии | committee report |
| poprawka / wniosek mniejszości | поправка / предложение меньшинства | amendment / minority motion |
| wysłuchanie publiczne | публичные слушания | public hearing |
| głosowanie / uchwalono / odrzucono | голосование / принят / отклонён | vote / adopted / rejected |
| stanowisko Senatu | позиция Сената | Senate position |
| weto / wniosek o ponowne rozpatrzenie | вето | veto |
| Trybunał Konstytucyjny | Конституционный трибунал | Constitutional Tribunal |
| ogłoszenie w Dzienniku Ustaw | публикация в Дзеннике Устав | promulgation in the Journal of Laws |
| wejście w życie / vacatio legis | вступление в силу / отсрочка вступления | entry into force / vacatio legis |
| tekst jednolity | консолидированный текст | consolidated text |
| rozporządzenie | постановление (подзаконный акт) | regulation (secondary legislation) |
| kadencja / dyskontynuacja | каденция / прекращение работ с окончанием каденции | term / discontinuation |
| pilny | срочный | urgent |
| Marszałek Sejmu | Маршал Сейма | Speaker of the Sejm |
