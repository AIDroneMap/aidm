# Blend every feathered orthophoto source

Issue: [#13](https://github.com/AIDroneMap/aidm/issues/13).

Status: the maintainer approved removing the early exit.

## Problem

The feather pass of `modules/orthophoto.py:merge` stops when the number of
covered pixels equals the output block width. Those quantities have different
meanings. A block with one covered row can skip the remaining sources even
though their feathered pixels should contribute to the blend.

Changing the comparison to the block area would also skip later sources in
fully covered blocks. Coverage does not tell this pass whether blending is
finished. Existing merge tests expect those later sources to contribute.

## Change

Remove the feather pass's coverage check and early exit. Keep the input order,
blend arithmetic, first-pass coverage check and final cutline pass.
Remove the block-width variable that only the feather check uses.

Merged pixels change where the old feather pass stopped early. In those blocks,
the remaining sources now contribute their blends. Other blocks keep their
existing blends. File paths, options and raster metadata stay the same.

This is one bug fix and one pull request. It makes no performance claim.

## Validation

- Add synthetic RGBA inputs whose covered pixel count equals each block's width.
  Check the complete output against explicit expected colors and alpha values.
  Include full tiles, rectangular edge tiles and a single-row output, with one
  and two workers. The regression must fail before the fix and pass after it.
- Check that three feather sources contribute in order and that a partially
  transparent cutline blends last. Keep the existing first-pass read tests.
- Run the normal unit suite in the published image through GitHub Actions.
- Declare `Test depth: full`. Ask the maintainer to start the release dry run on
  the final branch head and link its results in the pull request.
- Obtain independent approval under `REVIEW.md` after the required checks pass.
