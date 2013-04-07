import collections
import hmac
import math
import random
import time

class PointCache(object):

  @staticmethod
  def UpdatePointInSector(app_id, subject_id, sector_id, zoom_level, point_id,
      priority):
    key = (app_id, subject_id, sector_id, zoom_level)
    points = PointCache._CACHE[key]
    return PointCache._UpdatePoint(points, point_id, priority)

  @staticmethod
  def GetPointsInSector(app_id, subject_id, sector_id, zoom_level):
    key = (app_id, subject_id, sector_id, zoom_level)
    points = PointCache._CACHE.get(key)
    if not points:
      return ()

    current_time = time.time()
    PointCache._RemoveExpiredPoints(points, current_time)
    return points

  _MAX_POINTS_PER_SECTOR = 125
  _TTL = 60

  _Point = collections.namedtuple('Point',
      ('point_id', 'priority', 'exp_time'))

  _CACHE = collections.defaultdict(list)

  @staticmethod
  def _RemoveExpiredPoints(points, current_time):
    points[:] = [p for p in points if p.exp_time > current_time]

  @staticmethod
  def _UpdatePoint(points, point_id, priority):
    current_time = time.time()
    PointCache._RemoveExpiredPoints(points, current_time)
    exp_time = int(current_time + PointCache._TTL)

    assert len(points) <= PointCache._MAX_POINTS_PER_SECTOR

    new_point = PointCache._Point(point_id, priority, exp_time)
    if not points:
      points.append(new_point)
      return True

    min_priority_index = 0
    min_priority = points[0].priority
    for i, p in enumerate(points):
      if p.point_id == point_id:
        points[i] = new_point
        return True
      if p.priority < min_priority:
        min_priority_index = i
        min_priority = p.priority

    if len(points) == PointCache._MAX_POINTS_PER_SECTOR:
      if min_priority < priority:
        points[min_priority_index] = new_point
        return True
      return False

    points.append(new_point)
    return True


class AppStorage(object):

  _APPS = {}
  _App = collections.namedtuple('App', ('auth_key', 'hmac_key',
      'max_zoom_level', 'points'))
  _Point = collections.namedtuple('Point', ('subjects', 'coord'))

  @staticmethod
  def CreateApp(app_id, max_zoom_level):
    auth_key = str(random.random())
    hmac_key = str(random.random())
    points = {}
    AppStorage._APPS[app_id] = AppStorage._App(auth_key, hmac_key,
        max_zoom_level, points)

  @staticmethod
  def GetAuthKey(app_id):
    return AppStorage._APPS[app_id].auth_key

  @staticmethod
  def GetHmacKey(app_id):
    return AppStorage._APPS[app_id].hmac_key

  @staticmethod
  def GetMaxZoomLevel(app_id):
    return AppStorage._APPS[app_id].max_zoom_level

  @staticmethod
  def AddPoint(app_id, point_id):
    AppStorage._APPS[app_id].points[point_id] = AppStorage._Point(
        subjects=tuple(), coord=None)

  @staticmethod
  def DeletePoint(app_id, point_id):
    del AppStorage._APPS[app_id].points[point_id]

  @staticmethod
  def SetPointSubjects(app_id, point_id, subjects):
    points = AppStorage._APPS[app_id].points
    points[point_id] = points[point_id]._replace(subjects=subjects)

  @staticmethod
  def GetPointSubjects(app_id, point_id):
    return AppStorage._APPS[app_id].points[point_id].subjects

  @staticmethod
  def SetPointCoord(app_id, point_id, coord):
    points = AppStorage._APPS[app_id].points
    points[point_id] = points[point_id]._replace(coord=coord)

  @staticmethod
  def GetPointsCoords(app_id, points_ids):
    points = AppStorage._APPS[app_id].points
    return tuple(
        (point_id, points[point_id].coord)
        for point_id in points_ids
        if point_id in points
    )

  @staticmethod
  def HasPoint(app_id, point_id):
    return point_id in AppStorage._APPS[app_id].points


class AuthUtils(object):

  @staticmethod
  def GetGeoAuthToken(app_id, point_id, method_id, params):
    exp_time = int(time.time() + AuthUtils._GEO_AUTH_TOKEN_TTL)
    msg = (app_id, point_id, method_id, params, exp_time)
    msg_hmac = AuthUtils._GetHmac(app_id, msg)
    return (msg, msg_hmac)

  @staticmethod
  def ValidateGeoAuthToken(geo_auth_token):
    msg, msg_hmac = geo_auth_token
    app_id, point_id, method_id, params, exp_time = msg
    AuthUtils._ValidateHmac(app_id, msg, msg_hmac)
    if exp_time < time.time():
      raise Exception('auth token has been expired')
    if not AppStorage.HasPoint(app_id, point_id):
      raise Exception('app_id=%r has no point with id=%r' % (app_id, point_id))
    return app_id, point_id, method_id, params

  @staticmethod
  def GetAppAuthToken(app_id):
    auth_key = AppStorage.GetAuthKey(app_id)
    return (app_id, auth_key)

  @staticmethod
  def ValidateAppAuthToken(app_auth_token):
    app_id, auth_key = app_auth_token
    if AppStorage.GetAuthKey(app_id) != auth_key:
      raise Exception('invalid app auth token')
    return app_id


  _GEO_AUTH_TOKEN_TTL = 3600

  @staticmethod
  def _GetHmac(app_id, msg):
    hmac_key = AppStorage.GetHmacKey(app_id)
    return hmac.new(hmac_key, str(msg)).digest()

  @staticmethod
  def _ValidateHmac(app_id, msg, msg_hmac):
    if AuthUtils._GetHmac(app_id, msg) != msg_hmac:
      raise Exception('invalid hmac')


class MethodId(object):
  UPDATE_POINT = 1
  NEAREST_POINTS = 2
  POINTS_COORDS = 3


class GeoApi(object):

  @staticmethod
  def Call(geo_auth_token, **kwargs):
    app_id, point_id, method_id, params = AuthUtils.ValidateGeoAuthToken(
        geo_auth_token)
    return _GEO_API_METHODS_TABLE[method_id](app_id, point_id, params, kwargs)

  # Earth radius in meters. See http://en.wikipedia.org/wiki/Earth_radius .
  _EARTH_RADIUS = 6371 * 1000

  _DEFAULT_POINTS_LIMIT = 100

  @staticmethod
  def _GetDistance(coord1, coord2):
    dx = coord1[0] - coord2[0]
    dy = coord1[1] - coord2[1]
    dz = coord1[2] - coord2[2]

    return pow(dx**2 + dy**2 + dz**2, 0.5)

  @staticmethod
  def _ToXYZ(coord):
    phi = coord[0] / 180.0 * math.pi
    gamma = coord[1] / 180.0 * math.pi

    assert -90 <= phi <= 90
    assert -180 <= gamma <= 180

    elevation = coord[2] / GeoApi._EARTH_RADIUS
    if elevation < -1:
      elevation = -1
    elif elevation > 1:
      elevation = 1
    r = 1 + elevation

    assert 0 <= r <= 2

    r_xy = r * math.cos(phi)

    z = r * math.sin(phi)
    x = r_xy * math.cos(gamma)
    y = r_xy * math.sin(gamma)

    x = 0.25 * x + 0.5
    y = 0.25 * y + 0.5
    z = 0.25 * z + 0.5

    assert 0 <= x <= 1
    assert 0 <= y <= 1
    assert 0 <= z <= 1

    return (x, y, z)

  @staticmethod
  def _FromXYZ(coord):
    x = coord[0] * 4 - 2
    y = coord[1] * 4 - 2
    z = coord[2] * 4 - 2

    assert -2 <= x <= 2
    assert -2 <= y <= 2
    assert -2 <= z <= 2

    r = GeoApi._GetDistance((0, 0, 0), (x, y, z))
    if not r:
      return (0, 0, -GeoApi._EARTH_RADIUS)

    phi = math.asin(z / r)
    r_xy = r * math.cos(phi)
    if not r_xy:
      gamma = 0
    else:
      gamma = math.asin(y / r_xy)
    if x < 0:
      if y > 0:
        gamma = math.pi - gamma
      else:
        gamma = -math.pi - gamma

    elevation =  (r - 1) * GeoApi._EARTH_RADIUS

    phi = phi * 180.0 / math.pi
    gamma = gamma * 180.0 / math.pi

    return (phi, gamma, elevation)

  @staticmethod
  def _GetTileSize(zoom_level):
    tiles_count = 1 << zoom_level
    tile_size = 1.0 / tiles_count

    return tile_size

  @staticmethod
  def _GetSectorId(coord, zoom_level):
    x, y, z = coord

    assert 0 <= x <= 1
    assert 0 <= y <= 1
    assert 0 <= z <= 1

    tiles_count = 1 << zoom_level

    x_id = min(tiles_count - 1, int(x * tiles_count))
    y_id = min(tiles_count - 1, int(y * tiles_count))
    z_id = min(tiles_count - 1, int(z * tiles_count))

    return (x_id, y_id, z_id, zoom_level)

  @staticmethod
  def _ExtendPoints(points_map, points):
    for p in points:
      point_id = p.point_id
      if point_id in points_map:
        continue
      points_map[point_id] = {
          'point_id': point_id,
          'priority': p.priority,
          'exp_time': p.exp_time,
      }

  @staticmethod
  def _AddMissingCoordAndDistance(app_id, points_map, coord):
    points_ids = tuple(
        point_id
        for point_id, p in points_map.items()
        if 'coord' not in p
    )
    for point_id, p_coord in AppStorage.GetPointsCoords(app_id, points_ids):
      p = points_map[point_id]
      p['coord'] = p_coord
      p['distance'] = GeoApi._GetDistance(coord, p_coord)

  @staticmethod
  def _FilterPoints(points, max_distance):
    return [
        p
        for p in points
        if p['distance'] < max_distance
    ]

  @staticmethod
  def _GetNearestSectorIds(sector_id):
    x_id, y_id, z_id, zoom_level = sector_id

    return (
      (x_id - 1, y_id - 1, z_id - 1, zoom_level),
      (x_id,     y_id - 1, z_id - 1, zoom_level),
      (x_id + 1, y_id - 1, z_id - 1, zoom_level),
      (x_id - 1, y_id,     z_id - 1, zoom_level),
      (x_id    , y_id,     z_id - 1, zoom_level),
      (x_id + 1, y_id,     z_id - 1, zoom_level),
      (x_id - 1, y_id + 1, z_id - 1, zoom_level),
      (x_id    , y_id + 1, z_id - 1, zoom_level),
      (x_id + 1, y_id + 1, z_id - 1, zoom_level),

      (x_id - 1, y_id - 1, z_id    , zoom_level),
      (x_id,     y_id - 1, z_id    , zoom_level),
      (x_id + 1, y_id - 1, z_id    , zoom_level),
      (x_id - 1, y_id,     z_id    , zoom_level),
      (x_id    , y_id,     z_id    , zoom_level),
      (x_id + 1, y_id,     z_id    , zoom_level),
      (x_id - 1, y_id + 1, z_id    , zoom_level),
      (x_id    , y_id + 1, z_id    , zoom_level),
      (x_id + 1, y_id + 1, z_id    , zoom_level),

      (x_id - 1, y_id - 1, z_id + 1, zoom_level),
      (x_id,     y_id - 1, z_id + 1, zoom_level),
      (x_id + 1, y_id - 1, z_id + 1, zoom_level),
      (x_id - 1, y_id,     z_id + 1, zoom_level),
      (x_id    , y_id,     z_id + 1, zoom_level),
      (x_id + 1, y_id,     z_id + 1, zoom_level),
      (x_id - 1, y_id + 1, z_id + 1, zoom_level),
      (x_id    , y_id + 1, z_id + 1, zoom_level),
      (x_id + 1, y_id + 1, z_id + 1, zoom_level),
    )

  @staticmethod
  def _GetPointCoord(app_id, point_id, kwargs):
    coord = kwargs.get('coord')
    if coord is not None:
      return GeoApi._ToXYZ(coord)
    _, coord = AppStorage.GetPointsCoords(app_id, [point_id])[0]
    return coord

  @staticmethod
  def _GetZoomLevel(app_id, kwargs):
    max_zoom_level = AppStorage.GetMaxZoomLevel(app_id)
    radius = kwargs.get('radius', 0)
    if radius <= 0:
      return max_zoom_level
    multiplier = (GeoApi._EARTH_RADIUS * 2) / radius
    zoom_level = int(math.log(multiplier, 2))
    if zoom_level < 0:
      return 0
    if zoom_level > max_zoom_level:
      return max_zoom_level
    return zoom_level

  @staticmethod
  def _UpdatePoint(app_id, point_id, params_unused, kwargs):
    coord = GeoApi._ToXYZ(kwargs['coord'])
    subjects = AppStorage.GetPointSubjects(app_id, point_id)
    AppStorage.SetPointCoord(app_id, point_id, coord)
    max_zoom_level = AppStorage.GetMaxZoomLevel(app_id)

    for subject_id, priority in subjects:
      zoom_level = max_zoom_level
      while True:
        sector_id = GeoApi._GetSectorId(coord, zoom_level)

        if not PointCache.UpdatePointInSector(app_id, subject_id, sector_id,
            zoom_level, point_id, priority):
          break

        if not zoom_level:
          break

        zoom_level -= 1

  @staticmethod
  def _NearestPoints(app_id, point_id, subject_id, kwargs):
    """
      kwargs may contain:
        * coord - coordinates used for distance calculation
        * radius - zoom radius
        * points_limit - the maximum number of points to return
    """
    coord = GeoApi._GetPointCoord(app_id, point_id, kwargs)
    points_limit = kwargs.get('points_limit', GeoApi._DEFAULT_POINTS_LIMIT)
    zoom_level = GeoApi._GetZoomLevel(app_id, kwargs)
    tile_size = GeoApi._GetTileSize(zoom_level)

    points_map = {}
    while True:
      sector_id = GeoApi._GetSectorId(coord, zoom_level)
      for s_sector_id in GeoApi._GetNearestSectorIds(sector_id):
        sector_points = PointCache.GetPointsInSector(app_id, subject_id,
            s_sector_id, zoom_level)
        GeoApi._ExtendPoints(points_map, sector_points)

      GeoApi._AddMissingCoordAndDistance(app_id, points_map, coord)
      filtered_points = GeoApi._FilterPoints(points_map.values(), tile_size)
      if len(filtered_points) > points_limit:
        break

      if not zoom_level:
        break

      zoom_level -= 1
      tile_size *= 2

    filtered_points.sort(key=lambda p: p['distance'])
    return tuple(
        {
            'point_id': p['point_id'],
            'coord': GeoApi._FromXYZ(p['coord']),
            'priority': p['priority'],
            'distance': p['distance'] * GeoApi._EARTH_RADIUS * 4,
        }
        for p in filtered_points[:points_limit]
    )

  @staticmethod
  def _PointsCoords(app_id, point_id, points_ids, kwargs):
    """
      kwargs may contain:
        * coord - coordinates used for distance calculation
        * radius - zoom radius
        * points_limit - the maximum number of points to return
    """
    coord = GeoApi._GetPointCoord(app_id, point_id, kwargs)
    points_limit = kwargs.get('points_limit', len(points_ids))
    radius = kwargs.get('radius', 0)
    points = [
        {
            'point_id': point_id,
            'coord': GeoApi._FromXYZ(p_coord),
            'distance': GeoApi._GetDistance(coord, p_coord) * GeoApi._EARTH_RADIUS * 4,
        }
        for point_id, p_coord in AppStorage.GetPointsCoords(app_id, points_ids)
    ]
    if radius > 0:
      points = [p for p in points if p['distance'] < radius]
    points.sort(key=lambda p: p['distance'])
    return points[:points_limit]


_GEO_API_METHODS_TABLE = {
    MethodId.UPDATE_POINT: GeoApi._UpdatePoint,
    MethodId.NEAREST_POINTS: GeoApi._NearestPoints,
    MethodId.POINTS_COORDS: GeoApi._PointsCoords,
}


class ManagementApi(object):

  @staticmethod
  def CreatePoint(app_auth_token, point_id):
    app_id = AuthUtils.ValidateAppAuthToken(app_auth_token)
    AppStorage.AddPoint(app_id, point_id)

  @staticmethod
  def DeletePoint(app_auth_token, point_id):
    app_id = AuthUtils.ValidateAppAuthToken(app_auth_token)
    AppStorage.DeletePoint(app_id, point_id)

  @staticmethod
  def SetPointSubjects(app_auth_token, point_id, subjects):
    app_id = AuthUtils.ValidateAppAuthToken(app_auth_token)
    AppStorage.SetPointSubjects(app_id, point_id, subjects)

  @staticmethod
  def GetUpdatePointAuthToken(app_auth_token, point_id):
    return ManagementApi._GetAuthToken(app_auth_token, point_id,
        MethodId.UPDATE_POINT, None)

  @staticmethod
  def GetNearestPointsAuthToken(app_auth_token, point_id, subject_id):
    return ManagementApi._GetAuthToken(app_auth_token, point_id,
        MethodId.NEAREST_POINTS, subject_id)

  @staticmethod
  def GetPointsCoordsAuthToken(app_auth_token, point_id, points_ids):
    return ManagementApi._GetAuthToken(app_auth_token, point_id,
        MethodId.POINTS_COORDS, points_ids)

  @staticmethod
  def _GetAuthToken(app_auth_token, point_id, method_id, params):
    app_id = AuthUtils.ValidateAppAuthToken(app_auth_token)
    return AuthUtils.GetGeoAuthToken(app_id, point_id, method_id, params)

################################################################################

import random
import time


MAX_ZOOM_LEVEL = 20
POINTS_COUNT = 4000
REQUESTS_COUNT = 10

app_id = 'foobar'
subject_id = 123
points_limit = 3

AppStorage.CreateApp(app_id, MAX_ZOOM_LEVEL)

app_auth_token = AuthUtils.GetAppAuthToken(app_id)

for point_id in range(POINTS_COUNT):
  priority = random.random()
  subjects = ((subject_id, priority),)
  ManagementApi.CreatePoint(app_auth_token, point_id)
  ManagementApi.SetPointSubjects(app_auth_token, point_id, subjects)
print '%d points created' % POINTS_COUNT
raw_input('press enter to continue')

start = time.time()
for point_id in range(POINTS_COUNT):
  auth_token = ManagementApi.GetUpdatePointAuthToken(app_auth_token,
      point_id)
  phi = math.asin(random.random() * 2 - 1) / math.pi * 180
  gamma = random.random() * 360 - 180
  elevation = random.random() * 10000 - 5000
  coord = (phi, gamma, elevation)
  GeoApi.Call(auth_token, coord=coord)
end = time.time()
print '%.0f UpdateProint reuqests/s' % (POINTS_COUNT / (end - start))
raw_input('press enter to continue')

start = time.time()
for _ in range(REQUESTS_COUNT):
  range_size = 10
  start_range = int(random.random() * POINTS_COUNT) - range_size
  end_range = start_range + int(random.random() * range_size) + 1
  point_id = int(random.random() * POINTS_COUNT)
  points = range(start_range, end_range)
  geo_auth_token = ManagementApi.GetPointsCoordsAuthToken(app_auth_token,
      point_id, points)
  coords = GeoApi.Call(geo_auth_token)
  print 'points=%r, coords=%r' % (points, coords)
end = time.time()
print '%.0f Points requests/s' % (REQUESTS_COUNT /  (end - start))
raw_input('press enter to continue')

start = time.time()
for _ in range(REQUESTS_COUNT):
  point_id = int(random.random() * POINTS_COUNT)
  geo_auth_token = ManagementApi.GetNearestPointsAuthToken(app_auth_token,
      point_id, subject_id)
  phi = math.asin(random.random() * 2 - 1) / math.pi * 180
  gamma = random.random() * 360 - 180
  elevation = random.random() * 10000 - 5000
  coord = (phi, gamma, elevation)
  points = GeoApi.Call(geo_auth_token, coord=coord, points_limit=points_limit,
      radius=100000)
  print 'coord=%r, points_limit=%r, points=%r' % (coord, points_limit, points)
end = time.time()
print '%.0f NearestPoints requests/s' % (REQUESTS_COUNT /  (end - start))
raw_input('press enter to continue')
