# Location inventory filtering

User-approved refinement, September 2026 (spec sections 4, 7, 14 and 15).

- Country filters include stock in every authorized descendant room and shelf/rack.
- Room filters include stock stored directly in the room and on its shelves/racks.
- Shelf/rack selection narrows to that location; selecting a room or shelf fills the country.
- Selecting a country clears narrower selections. Changing grid location filters replaces
  an older location shortcut constraint rather than silently intersecting unrelated locations.
- Both individual-item and quantity-stock screens offer the same country/location controls.
  Location settings provide stock-preview links, and inventory tabs preserve country/location.
- ID-based filters distinguish identically named rooms/shelves. Legacy text filters still work.
- All filters narrow already-authorized inventory queries; selecting an ancestor never grants
  access to unauthorized sibling rooms. Ancestor labels in pickers are context, not access grants.
- The display label is now **Shelf/Rack**, without “Floor”. Stored `rack_shelf` values are
  unchanged. A choices-only migration records the label change; historical ledger rows are untouched.
- New import templates use Shelf/Rack. Both former spreadsheet headings remain supported aliases.
