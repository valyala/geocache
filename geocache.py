import collections
import hmac
import math
import random
import time

class PointCache(object):

  _MAX_POINTS_PER_SECTOR = 125
  _TTL = 60

  _CachePoint = collections.namedtuple('CachePoint',
      ('point_id', 'coord', 'priority', 'exp_time'))

  _CACHE = collections.defaultdict(list)

  @staticmethod
  def _RemoveExpiredPoints(points, current_time):
    points[:] = (point for point in points if point.exp_time > current_time)

  @staticmethod
  def _UpdatePoint(points, point_id, coord, priority):
    current_time = time.time()

    PointCache._RemoveExpiredPoints(points, current_time)

    exp_time = current_time + PointCache._TTL

    assert len(points) <= PointCache._MAX_POINTS_PER_SECTOR

    min_priority_index = 0
    for i, point in enumerate(points):
      if point.point_id == point_id:
        points[i] = PointCache._CachePoint(point_id, coord, priority, exp_time)
        return True
      if point.priority < points[min_priority_index].priority:
        min_priority_index = i

    if len(points) == PointCache._MAX_POINTS_PER_SECTOR:
      if points[min_priority_index].priority < priority:
        points[min_priority_index] = PointCache._CachePoint(point_id, coord,
            priority, exp_time)
        return True
      return False

    point = PointCache._CachePoint(point_id, coord, priority, exp_time)
    points.append(point)
    return True

  @staticmethod
  def UpdatePointInSector(app_id, subject_id, sector_id, zoom_level, point_id,
      coord, priority):
    key = (app_id, subject_id, sector_id, zoom_level)

    points = PointCache._CACHE[key]

    return PointCache._UpdatePoint(points, point_id, coord, priority)

  @staticmethod
  def GetPointsInSector(app_id, subject_id, sector_id, zoom_level):
    key = (app_id, subject_id, sector_id, zoom_level)

    points = PointCache._CACHE.get(key)
    if not points:
      return ()

    current_time = time.time()

    PointCache._RemoveExpiredPoints(points, current_time)

    return points


class AppStorage(object):

  _APPS = {}

  _App = collections.namedtuple('App', ('auth_key', 'hmac_key',
      'max_zoom_level', 'points'))

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
    AppStorage._APPS[app_id].points[point_id] = []

  @staticmethod
  def AddSubject(app_id, point_id, subject_id, priority):
    AppStorage._APPS[app_id].points[point_id].append((subject_id, priority))

  @staticmethod
  def GetSubjects(app_id, point_id):
    return AppStorage._APPS[app_id].points[point_id]


class AuthUtils(object):

  @staticmethod
  def GetHmac(app_id, msg):
    hmac_key = AppStorage.GetHmacKey(app_id)
    return hmac.new(hmac_key, str(msg)).digest()

  @staticmethod
  def ValidateHmac(app_id, msg, msg_hmac):
    if AuthUtils.GetHmac(app_id, msg) != msg_hmac:
      raise Exception('invalid hmac')

  @staticmethod
  def GetGeoAuthToken(app_id, point_id, method_name, ttl):
    exp_time = time.time() + ttl
    msg = (app_id, point_id, method_name, exp_time)
    msg_hmac = AuthUtils.GetHmac(app_id, msg)
    return (msg, msg_hmac)

  @staticmethod
  def ValidateGeoAuthToken(geo_auth_token, method_name):
    msg, msg_hmac = geo_auth_token
    app_id, point_id, actual_method_name, exp_time = msg

    AuthUtils.ValidateHmac(app_id, msg, msg_hmac)

    if actual_method_name != method_name:
      raise Exception('method name mismatch')

    if exp_time < time.time():
      raise Exception('auth token has been expired')

    return app_id, point_id

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


class GeoApi(object):

  # Earth radius in meters. See http://en.wikipedia.org/wiki/Earth_radius .
  _EARTH_RADIUS = 6371 * 1000

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
  def _ExtendPoints(points, new_points, coord):
    for point in new_points:
      point_id = point.point_id
      existing_point = points.get(point_id)

      if existing_point and existing_point['exp_time'] > point.exp_time:
        continue

      points[point_id] = {
          'point_id': point.point_id,
          'coord': point.coord,
          'priority': point.priority,
          'distance': GeoApi._GetDistance(coord, point.coord),
          'exp_time': point.exp_time,
      }

  @staticmethod
  def _FilterPoints(points, max_distance):
    filtered_points = []
    for point_id, point in points.iteritems():
      if point['distance'] < max_distance:
        filtered_points.append(point)

    return filtered_points

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
  def GetNearestPoints(geo_auth_token, subject_id, coord, points_limit):
    app_id, _ = AuthUtils.ValidateGeoAuthToken(geo_auth_token,
        'GetNearestPoints')
    coord = GeoApi._ToXYZ(coord)

    zoom_level = AppStorage.GetMaxZoomLevel(app_id)
    tile_size = GeoApi._GetTileSize(zoom_level)

    points = {}
    while True:
      sector_id = GeoApi._GetSectorId(coord, zoom_level)

      for s_sector_id in GeoApi._GetNearestSectorIds(sector_id):
        sector_points = PointCache.GetPointsInSector(app_id, subject_id,
            s_sector_id, zoom_level)
        GeoApi._ExtendPoints(points, sector_points, coord)

      filtered_points = GeoApi._FilterPoints(points, tile_size)
      if len(filtered_points) > points_limit:
        break

      if not zoom_level:
        break

      zoom_level -= 1
      tile_size *= 2

    # Sort points by distance from the current coordinates and trim
    # excess points.
    filtered_points.sort(key=lambda point: point['distance'])

    return tuple({
        'point_id': point['point_id'],
        'coord': GeoApi._FromXYZ(point['coord']),
        'priority': point['priority'],
        'distance': point['distance'] * GeoApi._EARTH_RADIUS * 4,
    } for point in filtered_points[:points_limit])

  @staticmethod
  def UpdatePoint(geo_auth_token, coord):
    app_id, point_id = AuthUtils.ValidateGeoAuthToken(geo_auth_token,
        'UpdatePoint')
    coord = GeoApi._ToXYZ(coord)

    subjects = AppStorage.GetSubjects(app_id, point_id)

    max_zoom_level = AppStorage.GetMaxZoomLevel(app_id)

    for subject_id, priority in subjects:
      zoom_level = max_zoom_level
      while True:
        sector_id = GeoApi._GetSectorId(coord, zoom_level)

        if not PointCache.UpdatePointInSector(app_id, subject_id, sector_id,
            zoom_level, point_id, coord, priority):
          break

        if not zoom_level:
          break

        zoom_level -= 1


class ManagementApi(object):

  @staticmethod
  def CreatePoint(app_auth_token, point_id, subjects):
    app_id = AuthUtils.ValidateAppAuthToken(app_auth_token)

    AppStorage.AddPoint(app_id, point_id)
    for subject_id, priority in subjects:
      AppStorage.AddSubject(app_id, point_id, subject_id, priority)

  @staticmethod
  def GetGeoAuthToken(app_auth_token, point_id, method_name, ttl):
    app_id = AuthUtils.ValidateAppAuthToken(app_auth_token)

    return AuthUtils.GetGeoAuthToken(app_id, point_id, method_name, ttl)

################################################################################

import random
import time


MAX_ZOOM_LEVEL = 20
POINTS_COUNT = 40000
REQUESTS_COUNT = 1 * 1000

app_id = 'foobar'
subject_id = 123
points_limit = 10

AppStorage.CreateApp(app_id, MAX_ZOOM_LEVEL)

app_auth_token = AuthUtils.GetAppAuthToken(app_id)

for point_id in range(POINTS_COUNT):
  priority = random.random()
  subjects = ((subject_id, priority),)
  ManagementApi.CreatePoint(app_auth_token, point_id, subjects)
print '%d points created' % POINTS_COUNT

raw_input('press enter to continue')

start = time.time()
for point_id in range(POINTS_COUNT):
  geo_auth_token = ManagementApi.GetGeoAuthToken(app_auth_token, point_id,
      'UpdatePoint', 60)
  phi = math.asin(random.random() * 2 - 1) / math.pi * 180
  gamma = random.random() * 360 - 180
  elevation = random.random() * 10000 - 5000
  coord = (phi, gamma, elevation)
  GeoApi.UpdatePoint(geo_auth_token, coord)
end = time.time()
print '%.0f updates/s' % (POINTS_COUNT / (end - start))

raw_input('press enter to continue')

start = time.time()
for point_id in range(REQUESTS_COUNT):
  geo_auth_token = ManagementApi.GetGeoAuthToken(app_auth_token, point_id,
      'GetNearestPoints', 60)
  phi = math.asin(random.random() * 2 - 1) / math.pi * 180
  gamma = random.random() * 360 - 180
  elevation = random.random() * 10000 - 5000
  coord = (phi, gamma, elevation)
  points = GeoApi.GetNearestPoints(geo_auth_token, subject_id, coord,
      points_limit)
end = time.time()
print '%.0f requests/s' % (REQUESTS_COUNT /  (end - start))

raw_input('press enter to continue')
