# Form Layout

The form builder has one canvas beside the existing field palette. **Row** and
**Section** are draggable palette components, alongside field types and workflow
inputs. Drop a Row onto the canvas, select its column count or width ratio, then
drag fields directly into its column slots. Dropping a field opens the existing
field configuration dialog; saving places it in that slot, and cancelling leaves
the slot empty. Existing fields can move between slots, rows, and sections.

The bottom canvas dropzone always adds at form level, outside named sections.
A standalone field has no Row controls; only adding a Row creates a Row component.
Inside an empty dropzone the editor highlights the destination; between items it
shows an insertion line.

Rows offer one, two, three, or four equal columns, plus two-column width ratios.
A preset cannot remove occupied columns. Each column can hold one field; empty
slots are stored as `field: null` so the structure survives saving. Preview and
respondent forms hide empty rows and sections, retain the widths of empty slots
beside visible fields on desktop, and stack visible fields on small screens.

Field definitions remain in `form_schema.fields`; placement references their
names. Every real field must appear exactly once. Layout changes do not change
submitted parameter names, workflow bindings, or access rules. The field dialog,
row controls, and field move menus also work without dragging.

## Existing Installations

The database migration adds a nullable JSONB `forms.layout` column. Existing
forms need no backfill and retain their vertical layout when layout is omitted.
Opening an existing form or saving its metadata does not initialize a layout.
Authors opt into saved layout metadata by changing the form's structure in the
canvas. Field settings and submitted parameter names remain independent of
placement. Apply the normal platform database
migration when upgrading; no workflow or integration configuration changes are
required by this layout feature.

Existing manifests without layout remain valid. Customized layouts are included
in manifest serialization and Solution capture inside the portable form schema;
they carry no organization or role bindings. Install layout-enabled manifests on
a platform version that supports this schema. Older versions do not implement
the layout and must not be relied on to preserve it during import/export.
Downgrading the database migration removes the layout column and its metadata.

## Manifest Example

Inside the form's existing UUID entry in `.bifrost/forms.yaml`:

```yaml
form_schema:
  fields:
    - name: first_name
      type: text
      label: First Name
    - name: email
      type: email
      label: Email
    - name: notes
      type: textarea
      label: Notes
  layout:
    sections:
      - title: Contact Details
        description: How we can reach you.
        rows:
          - columns:
              - field: first_name
                width: 6
              - field: email
                width: 6
          - columns:
              - field: null
                width: 6
              - field: null
                width: 6
      - rows:
          - field: notes
```

The second row remains available for editing after a save/reload, but is omitted
from respondent forms until it contains a visible field.

The API validates layout references and widths. Empty rows and sections are
allowed for authoring; a saved layout must still place every defined field.

An entry without a section title or description is ungrouped form-level content.
The `field` placement is a standalone field; `columns` denotes an explicit Row.
No section or Row component is implied for a standalone field. Layout is still
optional, so saving metadata on a fields-only form does not convert its schema.

## CLI and MCP

The CLI accepts the complete schema through `--form-schema` JSON. MCP form
create/update tools accept `fields` plus an optional `layout` object using the
same structure above. Updating layout requires the complete field list. Replacing
fields without layout returns the form to a vertical layout; metadata-only
updates leave the saved schema untouched.
