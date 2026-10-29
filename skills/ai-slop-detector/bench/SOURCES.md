# Benchmark sources

Every passage is plain text, 120-400 words, cut at paragraph or sentence boundaries. Nothing else was edited except: Wikipedia citation markers like `[12]` were removed, Gutenberg line wraps were joined and `_italics_` underscores dropped, and web-page list items were kept as `- ` lines.

**Licenses.** The repository's MIT license does **not** cover the human passages. Wikipedia excerpts stay under [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) (attribution: the article's contributors, via the revision link, which includes the full history). U.S. federal government web pages are public domain in the United States (17 U.S.C. 105). The Project Gutenberg essays were published before 1927 and are public domain in the United States; the Gutenberg header, footer and trademark are not included. The LLM-written passages are covered by the repository's MIT license.

## Human-written (all published before 2022)

| id | split | hard | source | version | license |
|---|---|---|---|---|---|
| h01 | dev |  | Wikipedia, "Tropical cyclone" | [rev 997378866](https://en.wikipedia.org/w/index.php?title=Tropical_cyclone&oldid=997378866), 2020-12-31 | CC BY-SA 3.0 |
| h02 | test |  | Wikipedia, "Coral reef" | [rev 990971008](https://en.wikipedia.org/w/index.php?title=Coral_reef&oldid=990971008), 2020-11-27 | CC BY-SA 3.0 |
| h03 | dev |  | Wikipedia, "Photosynthesis" | [rev 997284976](https://en.wikipedia.org/w/index.php?title=Photosynthesis&oldid=997284976), 2020-12-30 | CC BY-SA 3.0 |
| h04 | test |  | Wikipedia, "Sourdough" | [rev 994627404](https://en.wikipedia.org/w/index.php?title=Sourdough&oldid=994627404), 2020-12-16 | CC BY-SA 3.0 |
| h05 | dev |  | Wikipedia, "Remote work" | [rev 996683875](https://en.wikipedia.org/w/index.php?title=Remote_work&oldid=996683875), 2020-12-28 | CC BY-SA 3.0 |
| h06 | test |  | Wikipedia, "Git" | [rev 997249931](https://en.wikipedia.org/w/index.php?title=Git&oldid=997249931), 2020-12-30 | CC BY-SA 3.0 |
| h07 | dev |  | Wikipedia, "Electric vehicle battery" | [rev 994542203](https://en.wikipedia.org/w/index.php?title=Electric_vehicle_battery&oldid=994542203), 2020-12-16 | CC BY-SA 3.0 |
| h08 | test |  | Wikipedia, "Mount St. Helens" | [rev 997470823](https://en.wikipedia.org/w/index.php?title=Mount_St._Helens&oldid=997470823), 2020-12-31 | CC BY-SA 3.0 |
| h09 | dev |  | Wikipedia, "Western honey bee" | [rev 997417957](https://en.wikipedia.org/w/index.php?title=Western_honey_bee&oldid=997417957), 2020-12-31 | CC BY-SA 3.0 |
| h10 | test |  | Wikipedia, "Utility cycling" | [rev 990720421](https://en.wikipedia.org/w/index.php?title=Utility_cycling&oldid=990720421), 2020-11-26 | CC BY-SA 3.0 |
| h11 | dev |  | Wikipedia, "Coffee roasting" | [rev 995404725](https://en.wikipedia.org/w/index.php?title=Coffee_roasting&oldid=995404725), 2020-12-20 | CC BY-SA 3.0 |
| h12 | test |  | Wikipedia, "Plate tectonics" | [rev 997417794](https://en.wikipedia.org/w/index.php?title=Plate_tectonics&oldid=997417794), 2020-12-31 | CC BY-SA 3.0 |
| h13 | dev |  | Wikipedia, "Public library" | [rev 997072369](https://en.wikipedia.org/w/index.php?title=Public_library&oldid=997072369), 2020-12-29 | CC BY-SA 3.0 |
| h14 | test |  | Wikipedia, "Marathon" | [rev 996981782](https://en.wikipedia.org/w/index.php?title=Marathon&oldid=996981782), 2020-12-29 | CC BY-SA 3.0 |
| h15 | dev |  | Wikipedia, "Compost" | [rev 997312355](https://en.wikipedia.org/w/index.php?title=Compost&oldid=997312355), 2020-12-30 | CC BY-SA 3.0 |
| h16 | test |  | Wikipedia, "Sicilian Defence" | [rev 997337149](https://en.wikipedia.org/w/index.php?title=Sicilian_Defence&oldid=997337149), 2020-12-31 | CC BY-SA 3.0 |
| h17 | dev |  | Wikipedia, "Hubble Space Telescope" | [rev 997377508](https://en.wikipedia.org/w/index.php?title=Hubble_Space_Telescope&oldid=997377508), 2020-12-31 | CC BY-SA 3.0 |
| h18 | test | yes | Wikipedia, "Digital transformation" | [rev 997404043](https://en.wikipedia.org/w/index.php?title=Digital_transformation&oldid=997404043), 2020-12-31 | CC BY-SA 3.0 |
| h19 | dev | yes | Wikipedia, "Agile software development" | [rev 997490844](https://en.wikipedia.org/w/index.php?title=Agile_software_development&oldid=997490844), 2020-12-31 | CC BY-SA 3.0 |
| h20 | test |  | National Park Service, <https://www.nps.gov/yell/learn/nature/hydrothermal-features.htm> | [Wayback snapshot 2020-06-01](http://web.archive.org/web/20200601210719/https://www.nps.gov/yell/learn/nature/hydrothermal-features.htm) | U.S. public domain |
| h21 | dev |  | NOAA National Weather Service, <https://www.weather.gov/safety/lightning-science-overview> | [Wayback snapshot 2020-10-17](http://web.archive.org/web/20201017173638/https://www.weather.gov/safety/lightning-science-overview) | U.S. public domain |
| h22 | test | yes | FEMA (Ready.gov), <https://www.ready.gov/hurricanes> | [Wayback snapshot 2020-06-01](http://web.archive.org/web/20200601224214/https://www.ready.gov/hurricanes) | U.S. public domain |
| h23 | dev |  | Centers for Disease Control and Prevention, <https://www.cdc.gov/handwashing/when-how-handwashing.html> | [Wayback snapshot 2020-06-02](http://web.archive.org/web/20200602012314/https://www.cdc.gov/handwashing/when-how-handwashing.html) | U.S. public domain |
| h24 | test |  | Wikipedia, "Mars 2020" | [rev 996981889](https://en.wikipedia.org/w/index.php?title=Mars_2020&oldid=996981889), 2020-12-29 | CC BY-SA 3.0 |
| h25 | dev |  | Wikipedia, "Electric vehicle battery" | [rev 994542203](https://en.wikipedia.org/w/index.php?title=Electric_vehicle_battery&oldid=994542203), 2020-12-16 | CC BY-SA 3.0 |
| h26 | test |  | U.S. Geological Survey, <https://www.usgs.gov/volcanoes/mount-st.-helens> | [Wayback snapshot 2021-12-09](http://web.archive.org/web/20211209175424/https://www.usgs.gov/volcanoes/mount-st.-helens) | U.S. public domain |
| h27 | dev | yes | Library of Congress, <https://www.loc.gov/about/> | [Wayback snapshot 2020-06-01](http://web.archive.org/web/20200601181655/https://www.loc.gov/about/) | U.S. public domain |
| h28 | test | yes | GSA Technology Transformation Services (Digital.gov), <https://www.digital.gov/about/> | [Wayback snapshot 2020-06-16](http://web.archive.org/web/20200616231323/https://digital.gov/about/) | U.S. public domain |
| h29 | dev |  | Jerome K. Jerome, "On Being Idle", *Idle Thoughts of an Idle Fellow* (1886) | [Project Gutenberg #849](https://www.gutenberg.org/ebooks/849) | public domain (U.S.) |
| h30 | test |  | Stephen Leacock, "My Financial Career", *Literary Lapses* (1910) | [Project Gutenberg #6340](https://www.gutenberg.org/ebooks/6340) | public domain (U.S.) |
| h31 | dev |  | G. K. Chesterton, "On Running After One's Hat", *All Things Considered* (1908) | [Project Gutenberg #11505](https://www.gutenberg.org/ebooks/11505) | public domain (U.S.) |

Hard human cases (formal, corporate or list-heavy, where a style scanner is most likely to be wrong): h18 and h19 (Wikipedia articles on buzzword-heavy topics), h22 (Ready.gov checklist page), h27 (Library of Congress "about" page), h28 (Digital.gov mission statement).

## LLM-written

a01-a31 were **written by an LLM for this benchmark** in October 2026, one per human topic (a01 matches h01, and so on). 26 imitate a default chat-assistant answer ("explain X", "write a short article about X"). Five are hard cases written as if the LLM had been prompted to sound casual: a04, a10, a14, a15, a29. None of them were edited after scoring.
