# RadModeling: draw points from a CSV (x, y, z in cm in the first three columns, one
# header line) as small spheres, e.g. the lost particles reported by F4Enix.
import csv

RADIUS_MM = 20.0
MAX_POINTS = 1000


def rm_load_points():
    path = rm_ask_file("Select the CSV of points", "CSV (*.csv)|*.csv")
    if not path:
        return
    count = 0
    with open(path) as infile:
        reader = csv.reader(infile)
        next(reader, None)
        for row in reader:
            if count >= MAX_POINTS:
                break
            try:
                x, y, z = [CM(float(v)) for v in row[:3]]
            except (ValueError, IndexError):
                continue
            SphereBody.Create(
                Point.Create(x, y, z),
                Point.Create(x, y, z + MM(RADIUS_MM)),
                ExtrudeType.ForceIndependent,
            )
            count += 1
    rm_message("%d points loaded (limit %d)." % (count, MAX_POINTS))


rm_load_points()
