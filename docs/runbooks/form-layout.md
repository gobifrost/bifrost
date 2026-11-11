# Form Layout

Form authors can opt into sections, rows, and field columns using **Customize
Layout** in the form builder. Sections have optional titles and descriptions.
Each row uses a twelve-column grid; field widths are Quarter (3), Third (4),
Half (6), Two Thirds (8), Three Quarters (9), or Full (12). Columns stack in
their configured order on small screens.

Field definitions remain in `form_schema.fields`. Layout only references their
names; it does not change submitted values, workflow bindings, or access rules.
Each field must appear exactly once, and a row's widths cannot exceed twelve.
The editor keeps layout references synchronized when fields change.

## Existing Installations

The database migration adds a nullable JSONB `forms.layout` column. Existing
forms need no backfill and retain their vertical layout when layout is omitted.
Authors opt into the new editor per form. Apply the normal platform database
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
```

The API validates layout references and widths. Empty rows and sections are
allowed for authoring; a saved layout must still place every defined field.
