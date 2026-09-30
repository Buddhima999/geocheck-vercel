from typing import Optional
from pydantic import BaseModel


class SettingsIn(BaseModel):
    overhead_threshold_m: float
    line_threshold_m: float
    address_tolerance_m: float


class SettingsOut(SettingsIn):
    pass


class ColumnMapping(BaseModel):
    fdp_id: Optional[str] = None
    fdp_lat: str
    fdp_lon: str
    cust_id: Optional[str] = None
    cust_name: Optional[str] = None
    address: Optional[str] = None
    cust_lat: str
    cust_lon: str
    line_distance: Optional[str] = None
