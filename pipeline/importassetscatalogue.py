#!/usr/bin/env python
"""
Asset Catalogue Import Script for Houdini

This script imports assets from a specified directory into a Houdini Asset Gallery database.
It follows Houdini's component builder directory structure, scanning for USD assets with
optional thumbnails and variants.

Expected directory structure (https://www.sidefx.com/docs/houdini/ref/panes/assetgallery.html#asset_dirs):
    assets/
        book/
            book.usdc
            thumbnail.jpg
        lamp/
            lamp.usdc
            thumbnail.png
        teapot/
            teapot.usd
            thumbnail.jpg
            variants/
                teapot_fancy.usd
                teapot_fancy_thumbnail.jpg
                teapot_green.usd
                teapot_green_thumbnail.png

Usage:
    # Unix/Linux/macOS:
    hython importassetcatalogue.py /path/to/assets /path/to/database.db

    # Windows:
    hython importassetcatalogue.py C:\path\to\assets C:\path\to\database.db

By default, thumbnail generation and case-insensitive matching are enabled.
Use --no-generate-thumbnails or --case-sensitive to disable these features.
"""
import hou

import argparse
import os
import time
from pathlib import Path

USD_EXTENSIONS = {'.usd', '.usda', '.usdc'}
# supported image extensions, probably many will work but limiting it here to what i've tested just to be safe
THUMBNAIL_EXTENSIONS = {'.jpg', '.png', '.jpeg'}

def create_or_open_database(database_path):
    """
    Create a new asset gallery database or open an existing one.
    """
    try:
        database_path = os.path.abspath(database_path)

        # ensure directory exists
        db_dir = os.path.dirname(database_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
            print(f"Created directory: {db_dir}")

        datasource = hou.AssetGalleryDataSource(database_path)

        if not datasource.isValid():
            print(f"ERROR: Failed to create/open database: {database_path}")
            return None

        print(f"Successfully opened database: {database_path}")
        print(f"  Read-only: {datasource.isReadOnly()}")
        print(f"  Existing items: {len(datasource.itemIds())}")

        return datasource

    except Exception as e:
        print(f"ERROR: Exception while opening database: {e}")
        return None


class AssetInfo:
    """Container for asset information following Houdini's component builder structure."""

    def __init__(self, directory, primary_file, thumbnail=None, variants=None):
        self.directory = directory
        self.primary_file = primary_file
        self.thumbnail = thumbnail
        self.variants = variants or []  # list of (variant_file, variant_thumbnail) tuples
        self.name = directory.name

    def __repr__(self):
        variant_count = len(self.variants)
        return f"AssetInfo('{self.name}', primary={self.primary_file.name}, variants={variant_count})"


def find_thumbnail(directory, base_name='thumbnail', case_insensitive=False):
    """
    Find a thumbnail file in the directory.
    """
    # Try exact match first
    for ext in THUMBNAIL_EXTENSIONS:
        thumbnail_path = directory / f"{base_name}{ext}"
        if thumbnail_path.exists() and thumbnail_path.is_file():
            return thumbnail_path

    # If not found and case-insensitive mode, search manually
    if case_insensitive:
        target_names = {f"{base_name}{ext}".lower() for ext in THUMBNAIL_EXTENSIONS}
        for file in directory.iterdir():
            if file.is_file() and file.name.lower() in target_names:
                return file

    return None


def scan_asset_directory(asset_dir, case_insensitive=False):
    """
    Scan a single asset directory following Houdini's component builder structure.
    """
    if not asset_dir.is_dir():
        return None

    asset_name = asset_dir.name

    # Look for primary USD file matching directory name
    primary_file = None
    for ext in USD_EXTENSIONS:
        candidate = asset_dir / f"{asset_name}{ext}"
        if candidate.exists() and candidate.is_file():
            primary_file = candidate
            break

    # handle case-insensitive case
    if not primary_file and case_insensitive:
        target_names = {f"{asset_name}{ext}".lower() for ext in USD_EXTENSIONS}
        for file in asset_dir.iterdir():
            if file.is_file() and file.name.lower() in target_names:
                primary_file = file
                break

    if not primary_file:
        return None

    thumbnail = find_thumbnail(asset_dir, 'thumbnail', case_insensitive)

    # look for variants subdirectory
    variants = []
    variants_dir = asset_dir / 'variants'
    if variants_dir.exists() and variants_dir.is_dir():
        for variant_file in variants_dir.iterdir():
            if variant_file.is_file() and variant_file.suffix in USD_EXTENSIONS:
                variant_name = variant_file.stem
                variant_thumbnail = find_thumbnail(variants_dir, f"{variant_name}_thumbnail", case_insensitive)
                variants.append((variant_file, variant_thumbnail))

    return AssetInfo(
        directory=asset_dir,
        primary_file=primary_file,
        thumbnail=thumbnail,
        variants=variants
    )


def scan_assets_directory(assets_dir, case_insensitive=False):
    """
    Scan a directory for assets following Houdini's component builder structure.
    """
    assets_dir = Path(assets_dir)

    if not assets_dir.exists():
        print(f"ERROR: Assets directory does not exist: {assets_dir}")
        return []

    if not assets_dir.is_dir():
        print(f"ERROR: Path is not a directory: {assets_dir}")
        return []

    assets = []

    # scan each subdirectory in the assets directory
    for item in assets_dir.iterdir():
        if item.is_dir():
            asset_info = scan_asset_directory(item, case_insensitive)
            if asset_info:
                assets.append(asset_info)

    assets.sort(key=lambda a: a.name) # sorting so process order is consistent, its not necessary.

    print(f"Found {len(assets)} valid asset directories in {assets_dir}")

    # print out summary
    total_variants = sum(len(a.variants) for a in assets)
    if total_variants > 0:
        print(f"  Including {total_variants} variant(s)")

    return assets


def get_existing_asset_paths(datasource):
    """
    Get a set of file paths for all existing assets in the database.
    """
    existing_paths = set()

    try:
        item_ids = datasource.itemIds()

        for item_id in item_ids:
            file_path = datasource.filePath(item_id)
            if file_path:
                abs_path = os.path.abspath(file_path)
                existing_paths.add(abs_path)

    except Exception as e:
        print(f"WARNING: Error reading existing assets: {e}")

    return existing_paths


def load_thumbnail(thumbnail_path, generate_if_missing=False, usd_file_path=None):
    """
    Load thumbnail image data from a file, optionally generating it if missing.
    """
    # try to load existing thumbnail
    if thumbnail_path and thumbnail_path.exists():
        try:
            with open(thumbnail_path, 'rb') as f:
                return f.read()
        except Exception as e:
            print(f"WARNING: Failed to load thumbnail {thumbnail_path}: {e}")

    # generate thumbnail otherwise
    if generate_if_missing and usd_file_path:
        return generate_thumbnail_from_usd(usd_file_path)

    return b''


def generate_thumbnail_from_usd(usd_file_path, resolution=(512, 512)):
    """
    Generate a thumbnail image by rendering a USD file in Houdini.
    """
    import tempfile
    import math

    # create temporary output file
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
        temp_image_path = tmp.name

    # need these here to destroy temp nodes
    stage_net = None
    reference_sops = None

    try:
        # create lopnet stage to render out thumbnail
        stage_net = hou.node(f"/obj").createNode("lopnet", f"temp_stage")
        reference_node = stage_net.createNode("reference", f"temp_ref")
        reference_node.parm("filepath1").set(str(usd_file_path))

        # create camera
        camera_node = stage_net.createNode("camera", f"temp_cam")
        camera_node.setInput(0, reference_node)
        camera_node.parm("primpath").set("/cameras/thumbnail_cam")

        # camera settings
        focal_length = 50  # mm
        horizontal_aperture = 20.955  # mm
        aspect_ratio = 1.0
        camera_node.parm("aspectratiox").set(1)
        camera_node.parm("aspectratioy").set(1)
        camera_node.parm("horizontalAperture").set(horizontal_aperture)
        camera_node.parm("focalLength").set(focal_length)

        # =================
        # Calculate camera position
        # =================
        # Create SOPs net to load in geo and get the geo info for camera calculations
        reference_sops = hou.node(f"/obj").createNode("geo", f"temp_sops")
        usdimport_node = reference_sops.createNode("usdimport", f"temp_usd_import_sops")
        usdimport_node.parm("filepath1").set(str(usd_file_path))
        unpackusd_node = reference_sops.createNode("unpackusd", f"temp_unpackusd")
        unpackusd_node.setInput(0, usdimport_node)
        unpackusd_node.parm("output").set("polygons")
        unpackusd_node_geo = unpackusd_node.geometry()

        bbox = unpackusd_node_geo.boundingBox()
        center = bbox.center()
        size = bbox.sizevec()

        # Configure look-at constraint to aim camera at object center
        camera_node.parm("lookatenable").set(1)
        camera_node.parm("lookatpositionx").set(center[0])
        camera_node.parm("lookatpositiony").set(center[1])
        camera_node.parm("lookatpositionz").set(center[2])

        # Calculate the bounding box diagonal (maximum extent of the object)
        bbox_diagonal = math.sqrt(size[0]**2 + size[1]**2 + size[2]**2)

        # Calculate horizontal and vertical field of view in radians
        # Formula: FOV = 2 * arctan(aperture / (2 * focal_length))
        horizontal_fov_rad = 2 * math.atan(horizontal_aperture / (2 * focal_length))
        vertical_fov_rad = 2 * math.atan((horizontal_aperture / aspect_ratio) / (2 * focal_length))

        # Use the smaller FOV to ensure the object fits in both dimensions
        fov_rad = min(horizontal_fov_rad, vertical_fov_rad)

        # Calculate distance needed to fit the entire object in frame
        # Formula: distance = (bbox_diagonal / 2) / tan(fov / 2)
        # Multiply by 1.1 to add 10% padding
        distance = (bbox_diagonal / 2) / math.tan(fov_rad / 2) * 1.1

        # Define camera rotation angles in radians
        # -30° pitch (looking down at the object)
        # 45° yaw (viewing from the side)
        rx_rad = math.radians(-30)
        ry_rad = math.radians(45)

        # Calculate camera offset from object center using spherical positioning
        # Step 1: Apply yaw rotation (rotate around Y axis to position camera at 45° angle)
        cam_offset_x = distance * math.sin(ry_rad)
        cam_offset_z = distance * math.cos(ry_rad)
        cam_offset_y = 0
        # Step 2: Apply pitch rotation (tilt camera position upward by rotating around X axis)
        cam_offset_y_final = cam_offset_y * math.cos(rx_rad) - cam_offset_z * math.sin(rx_rad)
        cam_offset_z_final = cam_offset_y * math.sin(rx_rad) + cam_offset_z * math.cos(rx_rad)

        # Calculate final world-space camera position by adding offset to object center
        cam_pos_x = center[0] + cam_offset_x
        cam_pos_y = center[1] + cam_offset_y_final
        cam_pos_z = center[2] + cam_offset_z_final

        # Apply the calculated position to the camera node
        camera_node.parm("tx").set(cam_pos_x)
        camera_node.parm("ty").set(cam_pos_y)
        camera_node.parm("tz").set(cam_pos_z)
        # =================
        # Calculate camera position END
        # =================

        # create karma render settings
        karma_settings = stage_net.createNode("karmarendersettings", f"temp_karma")
        karma_settings.setInput(0, camera_node)
        karma_settings.parm("camera").set("/cameras/thumbnail_cam")
        karma_settings.parm("res_mode").set("Manual")
        karma_settings.parm("res_mode").pressButton()
        karma_settings.parm("resolutionx").set(resolution[0])
        karma_settings.parm("resolutiony").set(resolution[1])
        karma_settings.parm("picture").set(temp_image_path)

        # create USD render rop
        usdrop = stage_net.createNode("usdrender_rop", f"temp_usdrender_rop")
        usdrop.setInput(0, karma_settings)
        usdrop.parm("execute").pressButton()

        # load the rendered image
        if os.path.exists(temp_image_path):
            with open(temp_image_path, 'rb') as f:
                thumbnail_data = f.read()
            print(f"    Generated thumbnail from USD ({len(thumbnail_data)} bytes)")
            return thumbnail_data
        else:
            print(f"    Thumbnail generation failed: output file not created")
            return b''
    except Exception as e:
        print(f"    Error generating thumbnail from USD: {e}")
        return b''
    finally:
        # Cleanup
        try:
            if stage_net:
                stage_net.destroy()
            if reference_sops:
                reference_sops.destroy()
        except:
            pass

        try:
            if temp_image_path and os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
        except:
            pass


def import_asset(datasource, asset_info, existing_paths, import_variants=True, generate_thumbnails=True, tags=None):
    """
    Import a single asset (and optionally its variants) into the database.
    """
    success = 0
    failed = 0
    skipped = 0

    # check if primary asset already exists
    primary_path = str(asset_info.primary_file.resolve())
    if primary_path in existing_paths:
        print(f"    Skipped primary: {asset_info.name}")
        skipped += 1
        return (success, failed, skipped)

    thumbnail_data = load_thumbnail(
        asset_info.thumbnail,
        generate_if_missing=generate_thumbnails,
        usd_file_path=asset_info.primary_file
    )

    creation_date = int(os.path.getctime(primary_path))

    # add primary asset to database
    try:
        item_id = datasource.addItem(
            label=asset_info.name,
            file_path=primary_path,
            thumbnail=thumbnail_data,
            type_name='asset',
            blind_data=b'',
            creation_date=creation_date
        )

        if item_id:
            metadata = {
                'file_size': os.path.getsize(primary_path),
                'extension': asset_info.primary_file.suffix,
                'imported_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'has_variants': len(asset_info.variants) > 0
            }
            datasource.setMetadata(item_id, metadata)

            if tags:
                for tag in tags:
                    datasource.addTag(item_id, tag)

            success += 1
            print(f"    Imported primary: {asset_info.name}")
        else:
            failed += 1
            print(f"    Failed to add primary: {asset_info.name}")
            return (success, failed, skipped)
    except Exception as e:
        failed += 1
        print(f"    Error importing {asset_info.name}: {e}")
        return (success, failed, skipped)

    # import variants if requested and available
    if import_variants and asset_info.variants:
        print(f"    Importing {len(asset_info.variants)} variant(s)...")
        for variant_file, variant_thumbnail in asset_info.variants:
            variant_path = str(variant_file.resolve())
            variant_name = variant_file.stem

            # check if variant already exists
            if variant_path in existing_paths:
                print(f"      Skipped variant: {variant_name}")
                skipped += 1
                continue

            variant_thumb_data = load_thumbnail(
                variant_thumbnail,
                generate_if_missing=generate_thumbnails,
                usd_file_path=variant_file
            )

            variant_label = f"{asset_info.name} ({variant_name})" # generate variant label
            variant_creation_date = int(os.path.getctime(variant_path))
            try:
                variant_id = datasource.addItem(
                    label=variant_label,
                    file_path=variant_path,
                    thumbnail=variant_thumb_data,
                    type_name='asset',
                    blind_data=b'',
                    creation_date=variant_creation_date
                )

                if variant_id:
                    variant_metadata = {
                        'file_size': os.path.getsize(variant_path),
                        'extension': variant_file.suffix,
                        'imported_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'is_variant': True,
                        'parent_asset': asset_info.name
                    }
                    datasource.setMetadata(variant_id, variant_metadata)

                    if tags:
                        for tag in tags:
                            datasource.addTag(variant_id, tag)

                    success += 1
                    print(f"      Imported variant: {variant_name}")
                else:
                    failed += 1
                    print(f"      Failed to add variant: {variant_name}")
            except Exception as e:
                failed += 1
                print(f"      Error importing variant {variant_name}: {e}")
    return (success, failed, skipped)


def import_assets(assets_dir, database_path, import_variants=True, case_insensitive=True, generate_thumbnails=True, tags=None):
    """
    Import assets from a directory into an asset gallery database.

    Follows Houdini's component builder directory structure:
    - Each asset directory contains a USD file matching the directory name
    - Optional thumbnail.jpg or thumbnail.png in the asset directory
    - Optional variants/ subdirectory with variant USD files and thumbnails

    Args:
        assets_dir: Path to directory containing asset subdirectories
        database_path: Path to the asset gallery database file
        import_variants: Whether to import variants from variants subdirectory (default: True)
        case_insensitive: If True, perform case-insensitive matching for filenames (default: True)
        generate_thumbnails: Whether to auto-generate thumbnails from USD if missing (default: True)
        tags: Optional list of tags to apply to all imported assets

    Returns:
        dict with 'success', 'failed', 'skipped' counts
    """
    print("\n" + "="*70)
    print("Asset Catalogue Import Script")
    print("="*70)

    # printing info at the end
    stats = {
        'success': 0,
        'failed': 0,
        'skipped': 0,
        'total': 0
    }

    datasource = create_or_open_database(database_path)
    if not datasource:
        return stats

    if datasource.isReadOnly():
        print("ERROR: Database is read-only. Cannot import assets.")
        return stats

    # scan assets
    assets = scan_assets_directory(assets_dir, case_insensitive)
    if not assets:
        print("No assets found to import.")
        return stats
    existing_paths = get_existing_asset_paths(datasource)

    # import each asset
    print("\nStarting import transaction...")
    datasource.startTransaction()
    try:
        
        for i, asset_info in enumerate(assets, 1):
            print(f"\n[{i}/{len(assets)}] Processing: {asset_info.name}")

            s, f, sk = import_asset(datasource, asset_info, existing_paths, import_variants, generate_thumbnails, tags)
            stats['success'] += s
            stats['failed'] += f
            stats['skipped'] += sk
            stats['total'] += 1

        # commit the transaction
        print("\n" + "-"*70)
        print("Committing transaction...")
        datasource.endTransaction(commit=True)
        print("Transaction committed successfully.")
    except Exception as e:
        # rollback on error
        print(f"\nERROR during import: {e}")
        print("Rolling back transaction...")
        datasource.endTransaction(commit=False)
        print("Transaction rolled back.")

    # print summary
    print("\n" + "="*70)
    print("Import Summary")
    print("="*70)
    print(f"  Assets processed:      {stats['total']}")
    print(f"  Successfully imported: {stats['success']}")
    print(f"  Failed:                {stats['failed']}")
    print(f"  Skipped (duplicates):  {stats['skipped']}")
    print("="*70 + "\n")

    return stats


def main():
    """Command-line interface for the import script."""
    parser = argparse.ArgumentParser(
        description='Import USD assets into a Houdini Asset Gallery database',
        epilog='''
Expected directory structure:
  assets/
    asset_name/
      asset_name.usd[c|a]
      thumbnail.jpg|png
      variants/
        variant_name.usd[c|a]
        variant_name_thumbnail.jpg|png

Examples (Unix/Linux/macOS):
  hython importassetcatalogue.py /path/to/assets /path/to/my_assets.db
  hython importassetcatalogue.py /path/to/assets /path/to/my_assets.db --no-variants
  hython importassetcatalogue.py /path/to/assets /path/to/my_assets.db --no-generate-thumbnails
  hython importassetcatalogue.py /path/to/assets /path/to/my_assets.db --case-sensitive
  hython importassetcatalogue.py /path/to/assets /path/to/my_assets.db --tags environment,props

Examples (Windows):
  hython importassetcatalogue.py C:\Assets C:\Data\my_assets.db
  hython importassetcatalogue.py C:\Assets C:\Data\my_assets.db --no-generate-thumbnails
  hython importassetcatalogue.py C:\Assets C:\Data\my_assets.db --tags environment,props

Note: Thumbnail generation and case-insensitive matching are enabled by default.
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        'assets_dir',
        help='Path to directory containing asset subdirectories'
    )

    parser.add_argument(
        'database_path',
        help='Path to the asset gallery database file'
    )

    parser.add_argument(
        '--no-variants',
        action='store_true',
        help='Skip importing variants from variants/ subdirectories'
    )

    parser.add_argument(
        '--case-sensitive',
        action='store_true',
        help='Use case-sensitive matching for asset filenames and thumbnails (case-insensitive by default)'
    )

    parser.add_argument(
        '--no-generate-thumbnails',
        action='store_true',
        help='Disable automatic thumbnail generation from USD files (enabled by default)'
    )

    parser.add_argument(
        '--tags',
        type=str,
        metavar='TAG1,TAG2,...',
        help='Comma-separated list of tags to apply to imported assets'
    )

    args = parser.parse_args()

    tags = None
    if args.tags:
        tags = [tag.strip() for tag in args.tags.split(',')]

    import_assets(
        assets_dir=args.assets_dir,
        database_path=args.database_path,
        import_variants=not args.no_variants,
        case_insensitive=not args.case_sensitive,
        generate_thumbnails=not args.no_generate_thumbnails,
        tags=tags
    )


if __name__ == '__main__':
    main()
