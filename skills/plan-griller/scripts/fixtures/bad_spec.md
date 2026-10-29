# Spec: Saved searches

## Problem

Users can't save searches.

## Goals

- Users can save a search and run it again later.

## Users and actors

- Signed-in user.

## Requirements

- **R1** Users must be able to save the current search with a name.
- **R2** The saved searches list should load fast and be user-friendly.
- **R3** The system must send alerts for new results via email, push, etc.
- **R1** Saved searches must sync across devices.
- Users could share a saved search, TBD.

## Acceptance criteria

- **AC1** (R1) Given a signed-in user on the results page, when they click Save and enter "Bikes", then "Bikes" appears in their saved searches.
- **AC2** (R2) The list loads quickly.
- **AC3** Given a saved search, when it is deleted, then it is gone.
- **AC4** (R9) Given a user, when they rename a search, then the new name shows.

## Edge cases and error states

- <what happens with an empty name>

## Data and API changes

## Rollout, migration and flags

- Ship it behind a flag.

## Open questions

- Q1 How many saved searches per user?
- Q2 Do alerts respect quiet hours? (owner: Lee)

## Test plan

- AC1: `test_save_search`
- AC2: `test_list_speed`
- AC7: `test_unknown`
