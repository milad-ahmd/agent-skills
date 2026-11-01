# Refactor patterns by problem type

Each pattern lists the signal in the report, the usual cause, the target shape and a first step that can merge on its own.

## Import cycle (file or module)

- **Signal**: `import cycle` or `module cycle` in the problems list. Eager cycles break module loading order and make every file in the loop part of every change.
- **Usual cause**: a shared type or helper lives in the higher-level file, so the lower one imports back up to reach it.
- **Target shape**: dependencies point one way. The shared piece moves down into its own module, or the lower module depends on an interface it owns and the higher one implements it (dependency inversion).
- **First step**: move the one function, type or constant on the back edge into a new module both sides can import. Keep a re-export in the old place.
- **Verify**: the cycle id is gone from the next report and the tests pass.

## God file

- **Signal**: `god file` (many lines, many importers), worse when it is also a `hotspot`.
- **Usual cause**: several features grew in one place because it was already imported everywhere.
- **Target shape**: one module per reason to change, each with a small interface. The old file becomes a thin facade until callers move.
- **First step**: extract the group of functions with the fewest internal calls into its own module and re-export it.
- **Verify**: line count and fan-in of the old file drop. Callers did not change in the first step.

## Busy hub (many importers, changes often)

- **Signal**: `busy hub`, or a hub with low instability and high churn.
- **Usual cause**: a stable-looking module that keeps getting new fields or flags. Each change ripples into every importer.
- **Target shape**: the parts that change move out of the hub. The hub keeps only what is stable (types, interfaces).
- **First step**: find which part of the file the last ten commits touched (`git log -p -- FILE`) and move that part out.

## Hidden coupling (change together, no import)

- **Signal**: `hidden coupling`, especially across modules.
- **Usual cause**: duplicated knowledge (the same enum, schema, URL, validation rule or message format written twice), or two halves of one concept split across folders.
- **Target shape**: the knowledge lives in one place that both import, or the two files move into one module if they are one concept.
- **First step**: read three of the shared commits, name the duplicated thing, and extract it into a module both import.
- **Verify**: the next commits touching one file no longer need the other. This shows up in the report after some weeks, not right away.

## Layer violation

- **Signal**: `layer violation` with `--layers`.
- **Usual cause**: a type or helper defined in a higher layer that a lower layer needs, or business logic sitting in the UI or the data layer.
- **Target shape**: the lower layer owns the type or interface. The higher layer passes in what the lower one needs.
- **First step**: move the imported type down a layer and re-export it from its old location.

## Shallow modules and pass-through layers

- **Signal**: small files with fan-in 1 and fan-out 1 in a chain, or wrappers whose functions only forward arguments.
- **Target shape**: fewer, deeper modules, each hiding a real decision behind a small interface (Ousterhout, "A Philosophy of Software Design").
- **First step**: inline one pass-through layer where it has a single caller.

## Domain boundaries

- **Signal**: most features touch every top-level folder (controllers, services, models), and hidden coupling runs across them.
- **Target shape**: code grouped by domain (billing, accounts, search) with a small public interface per domain, layered inside.
- **First step**: pick the domain with the most co-change, create its folder, and move one vertical slice. Do not move every domain at once.

## Sources for the metrics

- Afferent and efferent coupling and instability I = Ce / (Ca + Ce): Robert C. Martin's package metrics.
- Hotspots (churn × size) and change coupling from version control: Adam Tornhill, "Your Code as a Crime Scene".
- Deep and shallow modules: John Ousterhout, "A Philosophy of Software Design".
