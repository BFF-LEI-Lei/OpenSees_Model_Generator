"""
Building Modeler for OpenSeesPy ~ Modeler module
"""

#   __                 UC Berkeley
#   \ \/\   /\/\/\     John Vouvakis Manousakis
#    \ \ \ / /    \    Dimitrios Konstantinidis
# /\_/ /\ V / /\/\ \
# \___/  \_/\/    \/   April 2021
#
# https://github.com/ioannis-vm/OpenSeesPy_Building_Modeler

from __future__ import annotations
from dataclasses import dataclass, field
from functools import total_ordering
from itertools import count
import json
import numpy as np
from utility import transformations, common
from utility import trib_area_analysis
from utility import mesher
from utility import mesher_section_gen
from utility.graphics import preprocessing_3D
from utility.graphics import preprocessing_2D

_ids = count(0)

# pylint: disable=unsubscriptable-object
# pylint: disable=invalid-name


def previous_element(lst: list, obj):
    """
    Returns the previous object in a list
    given a target object, assuming it is in the list.
    If it is not, it returns None.
    If the target is the first object, it returns None.
    """
    try:
        idx = lst.index(obj)
    except ValueError:
        return None
    if idx == 0:
        return None
    return lst[idx - 1]


def point_exists_in_list(pt: np.ndarray,
                         pts: list[np.ndarray]) -> bool:
    """
    Determines whether a given list containing points
    (represented with numpy arrays) contains a point
    that is equal (with a fudge factor) to a given point.
    Args:
        pt (np.ndarray): A numpy array to look for
        pts (list[np.ndarray]): A list to search for pt
    """
    for other in pts:
        dist = np.linalg.norm(pt - other)
        if dist < common.EPSILON:
            return True
    return False


@dataclass
@total_ordering
class GridLine:
    """
    Gridlines can be used to
    speed up the definition of the model.
    They are defined as line segments that have a
    starting and an ending point. They do not have
    to be permanent. Gridlines can be defined
    and used temporarily to define some elements,
    and later be discarded or altered in order
    to define other elements.
    Attributes:
        tag (str): Name of the gridline
        start (list(float]): X,Y coordinates of starting point
        end ~ similar to start
        start_np (np.ndarray): Numpy array of starting point
        end_np ~ similar to start
        length (float): Length of the gridline
        direction (float): Direction (angle) measured
                  using a counterclockwise convention
                  with the global X axis corresponding to 0.
    """
    tag: str
    start: list[float]
    end: list[float]
    start_np: np.ndarray = field(init=False, repr=False)
    end_np:   np.ndarray = field(init=False, repr=False)
    length: float = field(init=False, repr=False)
    direction: float = field(init=False, repr=False)

    def __post_init__(self):
        self.start_np = np.array(self.start)
        self.end_np = np.array(self.end)
        self.length = np.linalg.norm(self.end_np - self.start_np)
        self.direction = (self.end_np - self.start_np) / self.length

    def __eq__(self, other):
        return self.tag == other.tag

    def __le__(self, other):
        return self.tag <= other.tag

    def intersect(self, grd: GridLine):
        """
        Obtain the intersection with
        another gridline (if it exists)

        Parameters:
            grd(GridLine): a gridline to intersect
        Returns:
            list[float]: Intersection point

        Derivation:
            If the intersection point p exists, we will have
            p = ra.origin + ra.dir * u
            p = rb.origin + rb.dir * v
            We determine u and v(if possible) and check
            if the intersection point lies on both lines.
            If it does, the lines intersect.
        """
        ra_dir = self.direction
        rb_dir = grd.direction
        mat = np.array(
            [
                [ra_dir[0], -rb_dir[0]],
                [ra_dir[1], -rb_dir[1]]
            ]
        )
        if np.abs(np.linalg.det(mat)) <= common.EPSILON:
            # The lines are parallel
            return None
        # Get the origins
        ra_ori = self.start_np
        rb_ori = grd.start_np
        # System left-hand-side
        bvec = np.array(
            [
                [rb_ori[0] - ra_ori[0]],
                [rb_ori[1] - ra_ori[1]],
            ]
        )
        # Solve to get u and v in a vector
        uvvec = np.linalg.solve(mat, bvec)
        # Terminate if the intersection point
        # does not lie on both lines
        if uvvec[0] < 0 - common.EPSILON:
            return None
        if uvvec[1] < 0 - common.EPSILON:
            return None
        if uvvec[0] > self.length + common.EPSILON:
            return None
        if uvvec[1] > grd.length + common.EPSILON:
            return None
        # Otherwise the point is valid
        pt = ra_ori + ra_dir * uvvec[0]
        return np.array([pt[0], pt[1]])


@dataclass
class GridSystem:
    """
    This class is a collector for the gridlines, and provides
    methods that perform operations using gridlines.
    """

    grids: list[GridLine] = field(default_factory=list)

    def add(self, grdl: "GridLine"):
        """
        Add a gridline in the grid system,
        if it is not already in
        """
        if grdl not in self.grids:
            self.grids.append(grdl)
        else:
            raise ValueError('Gridline already exists: '
                             + repr(grdl))
        self.grids.sort()

    def remove(self, grdl: "GridLine"):
        """
        Remove a gridline from the grid system
        """
        self.grids.remove(grdl)

    def intersection_points(self):
        """
        Returns a list of all the points
        defined by gridline intersections
        """
        pts = []  # intersection points
        for i, grd1 in enumerate(self.grids):
            for j in range(i+1, len(self.grids)):
                grd2 = self.grids[j]
                pt = grd1.intersect(grd2)
                if pt is not None:  # if an intersection point exists
                    # and is not already in the list
                    if not point_exists_in_list(pt, pts):
                        pts.append(pt)
        return pts

    def intersect(self, grd: GridLine):
        """
        Returns a list of all the points
        defined by the intersection of a given
        gridline with all the other gridlines
        in the gridsystem
        """
        pts = []  # intersection points
        for other_grd in self.grids:
            # ignore current grid
            if other_grd == grd:
                continue
            # get the intersection point, if any
            pt = grd.intersect(other_grd)
            if pt is not None:  # if there is an intersection
                # and is not already in the list
                if not point_exists_in_list(pt, pts):
                    pts.append(pt)
            # We also need to sort the list.
            # We do this by sorting the instersection points
            # by their distance from the current gridline's
            # starting point.
            distances = [np.linalg.norm(pt-grd.start_np)
                         for pt in pts]
            pts = [x for _, x in sorted(zip(distances, pts))]
        return pts

    def __repr__(self):
        out = "The building has " + \
            str(len(self.grids)) + " gridlines\n"
        for grd in self.grids:
            out += repr(grd) + "\n"
        return out


@dataclass
@total_ordering
class Group:
    """
    This class is be used to group together
    elements of any kind.
    """

    name: str
    elements: list = field(init=False)

    def __post_init__(self):
        self.elements = []

    def __eq__(self, other):
        return self.name == other.name

    def __le__(self, other):
        return self.name <= other.name

    def add(self, element):
        """
        Add an element in the group,
        if it is not already in
        """
        if element not in self.elements:
            self.elements.append(element)

    def remove(self, element):
        """
        Remove something from the group
        """
        self.elements.remove(element)


@dataclass
class Groups:
    """
    Stores the  groups of a building.
    No two groups can have the same name.
    Elements can belong in multiple groups.
    """

    group_list: list[Group] = field(default_factory=list)
    active:     list[Group] = field(default_factory=list)

    def add(self, grp: Group):
        """
        Adds a new element group

        Parameters:
            grp(Group): the element group to add
        """
        # Verify element group name is unique
        if grp in self.group_list:
            raise ValueError('Group name already exists: ' + repr(grp))
        # Append the new element group in the list
        self.group_list.append(grp)
        # Sort the element groups in ascending order (name-wise)
        self.group_list.sort()

    def set_active(self, names: list[str]):
        """
        Specifies the active groups(one or more).
        Adding any element to the building will also
        add that element to the active groups.
        The active groups can also be set to an empty list.
        In that case, new elements will not be added toa any groups.
        Args:
            names (list[str]): Names of groups to set as active
        """
        self.active = []
        found = False
        for name in names:
            for grp in self.group_list:
                if grp.name == name:
                    self.active.append(grp)
                    found = True
            if found is False:
                raise ValueError("Group " + name + " does not exist")

    def __repr__(self):
        out = "The building has " + \
            str(len(self.group_list)) + " groups\n"
        for grp in self.group_list:
            out += repr(grp) + "\n"
        return out


@dataclass
@total_ordering
class Node:
    """
    Node object.
    Attributes:
        uniq_id (int): unique identifier
        coords (np.ndarray): Coordinates of the location of the node
        restraint_type (str): Can be either "free", "pinned", or "fixed".
                       It can also be "parent" or "internal", but
                       this is only specified for nodes that are made
                       automatically.
        mass (np.ndarray): Mass with respect to the global coordinate system
                           (shape = 3 for all nodes except parent nodes, where
                            inertia terms are also present, and shape = 6).
        mass_fl ~ similar to mass, coming from a floor
        load (np.ndarray): Load with respect to the global coordinate system.
        load_fl (np.ndarray): similar to load, coming from the floors.
        tributary_area: This attribute holds the results of tributary area
                        analysis done inside the `preprocess` method of the
                        `building` objects, and is used to store the floor
                        area that corresponds to that node (if beams  with
                        offsets are connected to it)
    """

    coords: np.ndarray
    restraint_type: str = field(default="free")
    mass: np.ndarray = field(default_factory=lambda: np.zeros(shape=3))
    load: np.ndarray = field(default_factory=lambda: np.zeros(shape=6))
    load_fl: np.ndarray = field(default_factory=lambda: np.zeros(shape=6))
    tributary_area: float = field(default=0.00)

    def __post_init__(self):
        self.uniq_id = next(_ids)

    def __eq__(self, other):
        """
        For nodes, a fudge factor is used to
        assess equality.
        """
        p0 = np.array(self.coords)
        p1 = np.array(other.coords)
        return np.linalg.norm(p0 - p1) < common.EPSILON

    def __le__(self, other):
        d_self = self.coords[1] * common.ALPHA + self.coords[0]
        d_other = other.coords[1] * common.ALPHA + other.coords[0]
        return d_self <= d_other

    def load_total(self):
        """
        Returns the total load applied on the node,
        by summing up the floor's contribution to the
        generic component.
        """
        return self.load + self.load_fl

    def mass_total(self):
        return self.mass + self.mass_fl


@dataclass
class Nodes:
    """
    This class is a collector for the nodes, and provides
    methods that perform operations using nodes.
    """

    node_list: list[Node] = field(default_factory=list)

    def add(self, node: Node):
        """
        Add a node in the nodes collection,
        if it does not already exist
        """
        if node not in self.node_list:
            self.node_list.append(node)
        else:
            raise ValueError('Node already exists: '
                             + repr(node))
        self.node_list.sort()

    def __repr__(self):
        out = "The level has " + \
            str(len(self.node_list)) + " nodes\n"
        for node in self.node_list:
            out += repr(node) + "\n"
        return out


@dataclass
class Section:
    """
    Section object.
    The axes are defined in the same way as they are
    defined in OpenSees. The colors assigned to
    the axes for plotting follow the
    AutoCAD convention.

            y(green)
            ^         x(red)
            :       .
            :     .
            :   .
           ===
            | -------> z (blue)
           ===
    Attributes:
        uniq_id (int): unique identifier
        sec_type (str): Flag representing the type of section
                  (e.g. W -> steel W section)
        name (str): Unique name for the section
        material (Material): Material of the section
        mesh (mesher.Mesh): Mesh object defining the geometry
                            of the section
        properties (dict): Dictionary with geometric properties
                           needed for structural analysis.
                           These are:
                           A, Ix, Iy, J
    """
    sec_type: str
    name: str
    material: Material = field(repr=False)
    mesh: mesher.Mesh = field(default=None, repr=False)
    properties: dict = field(default=None, repr=False)

    def __post_init__(self):
        self.uniq_id = next(_ids)

    def __eq__(self, other):
        return (self.name == other.name)

    def subdivide_section(self, n_x=10, n_y=25, plot=False):
        """
        Used to define the fibers of fiber sections.
        Args:
            n_x (int): Number of spatial partitions in the x direction
            n_y (int): Number of spatial partitions in the y direction
            plot (bool): Plots the resulting polygons for debugging
        Returns:
            pieces (list[shapely_Polygon]): shapely_Polygon
                   objects that represent single fibers.
        """
        return mesher.subdivide_polygon(
            self.mesh.halfedges, n_x=n_x, n_y=n_y, plot=plot)

    def retrieve_offset(self, placement: str):
        """
        Obtain the necessary offset in the y-z plane
        (local system)
        such that the element of that section has
        the specified placement point.
        The offset is expressed as the vector that moves
        from the placement point to the centroid.
        Args:
            placement (str): Can be one of:
                'centroid', 'top_center', 'top_left', 'top_right',
                'center_left', 'center_right', 'bottom_center',
                'bottom_left', 'bottom_right'
        """
        bbox = self.mesh.bounding_box()
        z_min, y_min, z_max, y_max = bbox.flatten()
        assert placement in ['centroid',
                             'top_center',
                             'top_left',
                             'top_right',
                             'center_left',
                             'center_right',
                             'bottom_center',
                             'bottom_left',
                             'bottom_right'], \
            "Invalid placement"
        if placement == 'centroid':
            return - np.array([0., 0.])
        elif placement == 'top_center':
            return - np.array([0., y_max])
        elif placement == 'top_left':
            return - np.array([z_min, y_max])
        elif placement == 'top_right':
            return - np.array([z_max, y_max])
        elif placement == 'center_left':
            return - np.array([z_min, 0.])
        elif placement == 'center_right':
            return - np.array([z_max, 0.])
        elif placement == 'bottom_center':
            return - np.array([0., y_min])
        elif placement == 'bottom_left':
            return - np.array([z_min, y_min])
        elif placement == 'bottom_right':
            return - np.array([z_max, y_min])


@dataclass
class Sections:
    """
    This class is a collector for sections.
    """

    section_list: list[Section] = field(default_factory=list)
    active: Section = field(default=None, repr=False)

    def add(self, section: Section):
        """
        Add a section in the section collection,
        if it does not already exist
        """
        if section not in self.section_list:
            self.section_list.append(section)
        else:
            raise ValueError('Section already exists: '
                             + repr(section))

    def set_active(self, name: str):
        """
        Sets the active section.
        Any elements defined while this section is active
        will have that section.
        Args:
            name (str): Name of the previously defined
                 section to set as active.
        """
        self.active = None
        found = False
        for section in self.section_list:
            if section.name == name:
                self.active = section
                found = True
        if found is False:
            raise ValueError("Section " + name + " does not exist")

    def __repr__(self):
        out = "Defined sections: " + str(len(self.section_list)) + "\n"
        for section in self.section_list:
            out += repr(section) + "\n"
        return out

    ####################
    # Shape generators #
    ####################

    def generate_W(self,
                   name: str,
                   material: Material,
                   properties: dict):
        """
        Generate a W section with specified parameters
        and add it to the sections list.
        """
        b = properties['bf']
        h = properties['d']
        tw = properties['tw']
        tf = properties['tf']
        mesh = mesher_section_gen.w_mesh(b, h, tw, tf)
        section = Section('W', name, material, mesh, properties)
        self.add(section)

    def generate_HSS(self,
                     name: str,
                     material: Material,
                     properties: dict):
        """
        Generate a HSS with specified parameters
        and add it to the sections list.
        """
        # use the name to assess whether it's a rectangular
        # or circular section
        xs = name.count('X')
        if xs == 2:
            # it's a rectangular section
            ht = properties['Ht']
            b = properties['B']
            t = properties['tdes']
            mesh = mesher_section_gen.HSS_rect_mesh(ht, b, t)
            section = Section('HSS', name, material, mesh, properties)
            self.add(section)
        elif xs == 1:
            # it's a circular section
            od = properties['OD']
            tdes = properties['tdes']
            n_pts = 25
            mesh = mesher_section_gen.HSS_circ_mesh(od, tdes, n_pts)
            section = Section('HSS', name, material, mesh, properties)
            self.add(section)
        else:
            raise ValueError("This should never happen...")

    def generate_rect(self,
                      name: str,
                      material: Material,
                      properties: dict):
        """
        Generate a rectangular section with specified
        parameters and add it to the sections list.
        """
        b = properties['b']
        h = properties['h']
        mesh = mesher_section_gen.rect_mesh(b, h)
        section = Section('rect', name, material, mesh, properties)
        self.add(section)
        temp = mesh.geometric_properties()
        properties['A'] = temp['area']
        properties['Ix'] = temp['inertia']['ixx']
        properties['Iy'] = temp['inertia']['iyy']
        properties['J'] = h * b**3 *\
            (16./3. - 3.36 * b/h * (1 - b**4/(12.*h**4)))


@dataclass
class Material:
    """
    Material object.
    Attributes:
        uniq_id (int): unique identifier
        name (str): Name of the material
        ops_material (str): Name of the material model to use in OpenSees
        density (float): Mass per unit volume of the material
        parameters (dict): Parameters needed to define the material in OpenSees
                           These depend on the meterial model specified.
    """
    name: str
    ops_material: str
    density: float  # mass per unit volume, specified in lb-s**2/in**4
    parameters: dict = field(repr=False)

    def __post_init__(self):
        self.uniq_id = next(_ids)


@dataclass
class Materials:
    """
    This class is a collector for materials.
    """

    material_list: list[Material] = field(default_factory=list)
    active: Material = field(default=None)

    def add(self, material: Material):
        """
        Add a material in the materials collection,
        if it does not already exist
        """
        if material not in self.material_list:
            self.material_list.append(material)
        else:
            raise ValueError('Material already exists: '
                             + repr(material))

    def set_active(self, name: str):
        """
        Assigns the active material.
        """
        self.active = None
        found = False
        for material in self.material_list:
            if material.name == name:
                self.active = material
                found = True
        if found is False:
            raise ValueError("Material " + name + " does not exist")

    def enable_Steel02(self):
        """
        Adds a predefined A992Fy50 steel material modeled
        using Steel02.
        """
        # units: lb, in
        self.add(Material('steel',
                          'Steel02',
                          0.0007344714506172839,
                          {
                              'Fy': 50000,
                              'E0': 29000000,
                              'G':   11153846.15,
                              'b': 0.01
                          })
                 )

    def __repr__(self):
        out = "Defined sections: " + str(len(self.material_list)) + "\n"
        for material in self.material_list:
            out += repr(material) + "\n"
        return out


@dataclass
class LinearElement:
    """
    Linear finite element class.
    This class represents the most primitive linear element,
    on which more complex classes build upon.
    Attributes:
        uniq_id (int): unique identifier
        node_i (Node): Node if end i
        node_j (Node): Node of end j
        section (Section): Section of the element.
        ang: Parameter that controls the rotation of the
             section around the x-axis
        offset_i (np.ndarray): Components of the vector that starts
                               from the primary node i and goes to
                               the first internal node at the end i.
                               Expressed in the global coordinate system.
        offset_j (np.ndarray): Similarly for node j
        internal_pt_i (np.ndarray): Coordinates of the internal point i
        internal_pt_j (np.ndarray): Similarly for node j
        udl (np.ndarray): Array of size 3 containing components of the
                          uniformly distributed load that is applied
                          to the clear length of the element, acting
                          on the local x, y, and z directions, in the
                          direction of the axes (see Section).
                          Values are in units of force per unit length.
        udl_fl (np.ndarray): Similar to udl, coming from the floors.
        x_axis: Array of size 3 representing the local x axis vector
                expressed in the global coordinate system.
        y_axis: (similar)
        z_axis: (similar).
                The axes are defined in the same way as they are
                defined in OpenSees.

                        y(green)
                        ^         x(red)
                        :       .
                        :     .
                        :   .
                       ===
                        | -------> z (blue)
                       ===

    """

    node_i: Node
    node_j: Node
    section: Section
    ang: float = field(default=0.00)
    offset_i: np.ndarray = field(default_factory=lambda: np.zeros(shape=3))
    offset_j: np.ndarray = field(default_factory=lambda: np.zeros(shape=3))
    udl: np.ndarray = field(default_factory=lambda: np.zeros(shape=3))
    udl_fl: np.ndarray = field(default_factory=lambda: np.zeros(shape=3))

    def __post_init__(self):
        self.uniq_id = next(_ids)
        # local axes with respect to the global coord system
        self.internal_pt_i = self.node_i.coords + self.offset_i
        self.internal_pt_j = self.node_j.coords + self.offset_j
        self.x_axis, self.y_axis, self.z_axis = \
            transformations.local_axes_from_points_and_angle(
                self.internal_pt_i, self.internal_pt_j, self.ang)

    def length_clear(self):
        """
        Computes the clear length of the element, excluding the offsets.
        Returns:
            float: distance
        """
        p_i = self.node_i.coords + self.offset_i
        p_j = self.node_j.coords + self.offset_j
        return np.linalg.norm(p_j - p_i)

    def add_udl_glob(self, udl: np.ndarray, ltype='generic'):
        """
        Adds a uniformly distributed load
        to the existing udl of the element.
        The load is defined
        with respect to the global coordinate system
        of the building, and it is converted to the
        local coordinate system prior to adding it.
        Args:
            udl (np.ndarray): Array of size 3 containing components of the
                              uniformly distributed load that is applied
                              to the clear length of the element, acting
                              on the global x, y, and z directions, in the
                              direction of the global axes.
        """
        T_mat = transformations.transformation_matrix(
            self.x_axis, self.y_axis, self.z_axis)
        udl_local = T_mat @ udl
        if ltype == 'generic':
            self.udl += udl_local
        elif ltype == 'floor':
            self.udl_fl += udl_local
        else:
            raise ValueError("Unsupported load type")

    def udl_total(self):
        """
        Returns the total udl applied to the element,
        by summing up the floor's contribution to the
        generic component.
        """
        return self.udl + self.udl_fl


@dataclass
@total_ordering
class BeamColumn:
    """
    A BeamColumn element here represents a collection
    of linear elements connected in series.
    Attributes:
        uniq_id (int): unique identifier
        node_i (int): primary node for end i
        node_j (int): primary node for end j
        ang: Parameter that controls the rotation of the
             section around the x-axis
        section (Section): Section of the element.
        n_sub (int): Number of linear elements between
                     the primary nodes node_i and node_j.
        placement (str): String flag that controls the
                         placement point of the element relative
                         to its section.
        offset_i (list[float]): Components of the vector that starts
                                from the primary node i and goes to
                                the first internal node at the end i.
                                Expressed in the global coordinate system.
        offset_j (list[float]): Similarly for node j.
        internal_pt_i (np.ndarray): Coordinates of the internal point i
        internal_pt_j (np.ndarray): Similarly for node j
        internal_nodes (list[Node]): Structural nodes needed to connect
                                     internal elements if more than one
                                     internal elements are present
                                     (n_sub > 1).
        internal_elems (list[LinearElement]): Internal linear elements.
        tributary_area (float): Area of floor that is supported on the element.
    """

    node_i: Node
    node_j: Node
    ang: float
    section: Section
    n_sub: int
    placement: str = field(default="centroid")
    offset_i: np.ndarray = field(default_factory=lambda: np.zeros(shape=3))
    offset_j: np.ndarray = field(default_factory=lambda: np.zeros(shape=3))
    tributary_area: float = field(default=0.00)

    def __post_init__(self):

        self.uniq_id = next(_ids)

        p_i = self.node_i.coords + self.offset_i
        p_j = self.node_j.coords + self.offset_j

        # obtain offset from section (local system)
        dz, dy = self.section.retrieve_offset(self.placement)
        sec_offset_local = np.array([0.00, dy, dz])
        # retrieve local coordinate system
        x_axis, y_axis, z_axis = \
            transformations.local_axes_from_points_and_angle(
                p_i, p_j, self.ang)
        # add the offset due to the section's placement point
        # to the user defined offsets
        t_glob_to_loc = transformations.transformation_matrix(
            x_axis, y_axis, z_axis)
        t_loc_to_glob = t_glob_to_loc.T
        sec_offset_global = t_loc_to_glob @ sec_offset_local
        p_i += sec_offset_global
        p_j += sec_offset_global
        self.offset_i = self.offset_i.copy()
        self.offset_j = self.offset_j.copy()
        self.offset_i += sec_offset_global
        self.offset_j += sec_offset_global

        internal_pt_coords = np.linspace(
            tuple(p_i), tuple(p_j), num=self.n_sub+1)
        self.internal_pt_i = internal_pt_coords[0]
        self.internal_pt_j = internal_pt_coords[-1]
        self.internal_nodes = []
        self.internal_elems = []
        # internal nodes (if required)
        if self.n_sub > 1:
            for i in range(1, len(internal_pt_coords)-1):
                self.internal_nodes.append(Node(internal_pt_coords[i]))
        # internal elements
        for i in range(self.n_sub):
            if i == 0:
                node_i = self.node_i
                ioffset = self.offset_i
            else:
                node_i = self.internal_nodes[i-1]
                ioffset = np.zeros(3).copy()
            if i == self.n_sub-1:
                node_j = self.node_j
                joffset = self.offset_j
            else:
                node_j = self.internal_nodes[i]
                joffset = np.zeros(3).copy()
            self.internal_elems.append(
                LinearElement(node_i, node_j, self.section, self.ang,
                              ioffset, joffset))

    def length_clear(self):
        pt_i = self.internal_pt_i
        pt_j = self.internal_pt_j
        return np.linalg.norm(pt_j - pt_i)

    def add_udl_glob(self, udl: np.ndarray, ltype='generic'):
        """
        Adds a uniformly distributed load
        to the existing udl of the element.
        The load is defined
        with respect to the global coordinate system
        of the building, and it is converted to the
        local coordinate system prior to adding it.
        Args:
            udl (np.ndarray): Array of size 3 containing components of the
                              uniformly distributed load that is applied
                              to the clear length of the element, acting
                              on the global x, y, and z directions, in the
                              direction of the global axes.
        """
        for elm in self.internal_elems:
            elm.add_udl_glob(udl, ltype=ltype)

    def apply_self_weight_and_mass(self, multiplier: float):
        """
        Applies self-weight as a and distributes mass
        by lumping it at the nodes where the ends of the
        internal elements are connected.
        Args:
            multiplier: A parameter that is multiplied to the
                        automatically obtained self-weight and self-mass.
        """
        if multiplier == 0.:
            return
        cross_section_area = self.section.properties["A"]
        mass_per_length = cross_section_area * \
            self.section.material.density              # lb-s**2/in**2
        weight_per_length = mass_per_length * common.G_CONST  # lb/in
        mass_per_length *= multiplier
        weight_per_length *= multiplier
        self.add_udl_glob(
            np.array([0., 0., -weight_per_length]), ltype='generic')
        for sub_elm in self.internal_elems:
            mass = mass_per_length * \
                sub_elm.length_clear() / 2.00  # lb-s**2/in
            sub_elm.node_i.mass += np.array([mass, mass, mass])
            sub_elm.node_j.mass += np.array([mass, mass, mass])

    def __eq__(self, other):
        return (self.node_i == other.node_i and
                self.node_j == other.node_j)

    def __le__(self, other):
        return self.node_i <= other.node_i


@dataclass
class BeamColumns:
    """
    This class is a collector for columns, and provides
    methods that perform operations using columns.
    """

    element_list: list[BeamColumn] = field(default_factory=list)

    def add(self, elm: BeamColumn):
        """
        Add a column in the columns collection,
        if it does not already exist
        """
        if elm not in self.element_list:
            self.element_list.append(elm)
            self.element_list.sort()

    def __repr__(self):
        out = "The level has " + str(len(self.element_list)) + " elements\n"
        for elm in self.element_list:
            out += repr(elm) + "\n"
        return out


@dataclass
@total_ordering
class Level:
    """
    Individual building floor level.
    A level contains building components such as nodes, beams and columns.
    Attributes:
        name (str): Unique name of the level
        elevation (float): Elevation of the level
        restraint (str): Can be any of "free", "pinned" or "fixed".
                         All nodes defined in that level will have that
                         restraint.
        previous_lvl (Level): Points to the level below that level, if
                              the considered level is not the base..
        surface_DL (float): Uniformly distributed dead load of the level.
                            This load can be distributed to the
                            structural members of the level automatically.
                            It is also converted and applied as mass.
        nodes_primary (Nodes): Primary nodes of the level. Primary means
                               that these nodes are used to connect
                               components of different elements
                               to them, contrary to being internal
                               nodes of a particular
                               element. A rigid diaphragm constraint can be
                               optionally assigned to these nodes.
        columns (BeamColumns): Columns of the level.
        beams (BeamColumns): Beams of the level.
        parent_node (Node): If tributary area analysis is done and floors
                            are assumed, a node is created at the
                            center of mass of the level, and acts as the
                            parent node of the rigid diaphragm constraint.
                            The mass in the X-Y direction of all the nodes
                            of that level is then accumulated to that
                            node, together with their contribution in the
                            rotational inertia of the level.
        floor_coordinates (np.ndarray): An array of a sequence of
                          points that define the floor area that is
                          inferred from the beams if tributary area
                          analysis is done.
        floor_bisector_lines (np.ndarray): The lines used to separate
                             the tributary areas, used for plotting.
    """
    name: str
    elevation: float
    restraint: str = field(default="free")
    previous_lvl: 'Level' = field(default=None)
    surface_DL: float = field(default=0.00)
    nodes_primary: Nodes = field(default_factory=Nodes)
    columns: BeamColumns = field(default_factory=BeamColumns)
    beams: BeamColumns = field(default_factory=BeamColumns)
    parent_node: Node = field(default=None)
    floor_coordinates: np.ndarray = field(default=None)
    floor_bisector_lines: list[np.ndarray] = field(default=None)

    def __post_init__(self):
        if self.restraint not in ["free", "fixed", "pinned"]:
            raise ValueError('Invalid restraint type: ' + self.restraint)

    def __eq__(self, other):
        return self.name == other.name

    def __le__(self, other):
        return self.elevation <= other.elevation

    def look_for_node(self, x_coord: float, y_coord: float):
        """
        Returns the node that occupies a given point
        at the current level, if it exists
        """
        candidate_node = Node(np.array([x_coord, y_coord,
                                        self.elevation]), self.restraint)
        for other_node in self.nodes_primary.node_list:
            if other_node == candidate_node:
                return other_node
        return None

    def add_column(self, node_i, node_j, ang, section):
        """
        Adds a column on that level with given nodes.
        """
        # create the element
        col_to_add = BeamColumn(node_i, node_j, ang, section=section)
        # add the element to the level's columns
        self.columns.add(col_to_add)

    def add_beam(self, node_i, node_j, ang, section):
        """
        Adds a beam on that level with given nodes.
        """
        # create the element
        bm_to_add = BeamColumn(node_i, node_j, ang, section=section)
        # add the element to the level's beams
        self.beams.add(bm_to_add)

    def assign_surface_DL(self,
                          load_per_area: float):
        self.surface_DL = load_per_area

    def list_of_primary_nodes(self):
        return self.nodes_primary.node_list

    def list_of_all_nodes(self):
        """
        Returns a list containing all the nodes
        of that level *except* the parent node.
        """
        primary = self.nodes_primary.node_list
        internal = []
        for col in self.columns.element_list:
            internal.extend(col.internal_nodes)
        for bm in self.beams.element_list:
            internal.extend(bm.internal_nodes)
        result = [i for i in primary + internal if i]
        # (to remove Nones if they exist)
        return result

    def list_of_internal_elems(self):
        result = []
        for elm in self.beams.element_list + \
                self.columns.element_list:
            result.append(elm)
        return result


@dataclass
class Levels:
    """
    Stores the floor levels of a building.
    No two floor levels can have the same height(no multi-tower support).
    Levels must be defined in order, from the lower elevation
    to the highest.
    Attributes:
        level_list (list[Level]): list containing unique levels
        active (list[Level]): list of active levels
    """

    level_list: list[Level] = field(default_factory=list)
    active: list[Level] = field(default_factory=list)

    def add(self, lvl: Level):
        """
        Adds a new level. The levels must be added in ascending
        elevations.

        Parameters:
            lvl(Level): the level to add
        """
        # Verify level name is unique
        if lvl in self.level_list:
            raise ValueError('Level name already exists: ' + repr(lvl))
        # Verify level elevation is unique
        if lvl.elevation in [lev.elevation
                             for lev in self.level_list]:
            raise ValueError('Level elevation already exists: ' + repr(lvl))
        # Don't accept levels out of order
        if self.level_list:
            if lvl.elevation < self.level_list[-1].elevation:
                raise ValueError(
                    'Levels should be defined from the bottom up for now..')
        # Append the new level in the level list
        self.level_list.append(lvl)
        previous_lvl = previous_element(self.level_list, lvl)
        if previous_lvl:
            lvl.previous_lvl = previous_lvl

        # If there's no active level, make
        # the newly added level active
        if not self.active:
            self.active.append(lvl)
            self.active.sort()

    def get(self, name: str):
        """"
        Finds a level given its name.
        """
        for lvl in self.level_list:
            if lvl.name == name:
                return lvl
        raise ValueError("Level " + name + " does not exist")

    def set_active(self, names: list[str]):
        """
        Sets the active levels (one or more).
        At least one level must be active when defining elements.
        Any element addition or modification call will
        only affect the active levels.
        Args:
            names (list[str]): Names of the levels to set as active
        """
        self.active = []
        if names == "all":
            self.active = self.level_list
        elif names == "all_above_base":
            self.active = self.level_list[1::]
        else:
            for name in names:
                retrieved_level = self.get(name)
                if retrieved_level not in self.active:
                    self.active.append(
                        retrieved_level
                    )

    def __repr__(self):
        out = "The building has " + \
            str(len(self.level_list)) + " levels\n"
        for lvl in self.level_list:
            out += repr(lvl) + "\n"
        return out


@dataclass
class Building:
    """
    This class manages building objects.
    Attributes:
        gridsystem (GridSystem): Gridsystem used to
                   define or modify elements.
        levels (Levels): Levels of the building
        groups (Groups): Groups of the building
        sections (Sections): Sections used
        materials (Materials): Materials used
        active_placement (str): Placement parameter to use
                          for newly defined elements
                          where applicable (see Section).
        active_angle (float): Angle parameter to use for
                          newly defined elements.
    """
    gridsystem: GridSystem = field(default_factory=GridSystem)
    levels: Levels = field(default_factory=Levels)
    groups: Groups = field(default_factory=Groups)
    sections: Sections = field(default_factory=Sections)
    materials: Materials = field(default_factory=Materials)
    active_placement: str = field(default='centroid')
    active_angle: float = field(default=0.00)

    ###############################################
    # 'Add' methods - add objects to the building #
    ###############################################

    def add_node(self,
                 x: float,
                 y: float) -> list[Node]:
        """
        Adds a node at a particular point in all active levels.
        Returns all added nodes.
        """
        added_nodes = []
        for level in self.levels.active:
            node = Node([x, y,
                         level.elevation], level.restraint)
            level.nodes_primary.add(node)
            added_nodes.append(node)
        return added_nodes

    def add_level(self,
                  name: str,
                  elevation: float,
                  restraint: str = "free"
                  ) -> Level:
        """
        Adds a level to the building.
        Levels must be defined in increasing elevations.
        Args:
            name (str): Unique name of the level
            elevation (float): Elevation of the level
            restraint (str): Can be any of "free", "pinned" or "fixed".
                             All nodes defined in that level will have that
                             restraint.
        """
        level = Level(name, elevation, restraint)
        self.levels.add(level)
        return level

    def add_gridline(self,
                     tag: str,
                     start: list[float],
                     end: list[float]
                     ) -> GridLine:
        """
        Adds a new gridline to the building.
        Args:
           tag (str): Name of the gridline
           start (list(float]): X,Y coordinates of starting point
           end ~ similar to start
        Regurns:
            gridline object
        """
        gridline = GridLine(tag, start, end)
        self.gridsystem.add(gridline)
        return gridline

    def add_sections_from_json(self,
                               filename: str,
                               sec_type: str,
                               labels: list[str]):
        """
        Add sections from a section database json file.
        Only the specified sections(given the labels) are added,
        even if more are present in the file.
        Args:
            filename (str): Path of the file
            sec_type (str): Section type to be assigned
                            to all the defined sections
                            (see sections).
                            I.e. don't import W and HSS
                            sections at once!
            labels (list[str]): Names of the sections to add.
        """
        if not self.materials.active:
            raise ValueError("No active material specified")
        if sec_type == 'W':
            with open(filename, "r") as json_file:
                section_dictionary = json.load(json_file)
            for label in labels:
                try:
                    sec_data = section_dictionary[label]
                except KeyError:
                    raise KeyError("Section " + label + " not found in file.")
                self.sections.generate_W(label,
                                         self.materials.active,
                                         sec_data)
        if sec_type == "HSS":
            with open(filename, "r") as json_file:
                section_dictionary = json.load(json_file)
            for label in labels:
                try:
                    sec_data = section_dictionary[label]
                except KeyError:
                    raise KeyError("Section " + label + " not found in file.")
                self.sections.generate_HSS(label,
                                           self.materials.active,
                                           sec_data)

    def add_gridlines_from_dxf(self,
                               dxf_file: str) -> list[GridLine]:
        """
        Parses a given DXF file and adds gridlines from
        all the lines defined in that file.
        Args:
            dxf_file (str): Path of the DXF file.
        Returns:
            grds (list[GridLine]): Added gridlines
        """
        i = 100000  # anything > 8 works
        j = 0
        xi = 0.00
        xj = 0.00
        yi = 0.00
        yj = 0.00
        grds = []
        with open(dxf_file, 'r') as f:
            while True:
                ln = f.readline()
                if ln == "":
                    break
                ln = ln.strip()
                if ln == "AcDbLine":
                    i = 0
                if i == 2:
                    xi = float(ln)
                if i == 4:
                    yi = float(ln)
                if i == 6:
                    xj = float(ln)
                if i == 8:
                    yj = float(ln)
                    grd = self.add_gridline(str(j), [xi, yi], [xj, yj])
                    grds.append(grd)
                    j += 1
                i += 1
        return grds

    def add_group(self, name: str) -> Group:
        """
        Adds a new group to the building.
        Args:
            name: Name of the group to be added.
        Returns:
            group (Group): Added group.
        """
        group = Group(name)
        self.groups.add(group)
        return group

    def add_column_at_point(self,
                            x: float,
                            y: float,
                            n_sub=1) -> list[BeamColumn]:
        """
        Adds a vertical column at the given X, Y
        location at all the active levels.
        Existing nodes are used, otherwise they are created.
        Args:
            x (float): X coordinate in the global system
            y (float): Y coordinate in the global system
            n_sub (int): Number of internal elements to add
        Returns:
            columns (list[BeamColumn]): Added columns.
        """
        if not self.sections.active:
            raise ValueError("No active section")
        columns = []
        for level in self.levels.active:
            if level.previous_lvl:  # if previous level exists
                # check to see if top node exists
                top_node = level.look_for_node(x, y)
                # create it if it does not exist
                if not top_node:
                    top_node = Node(
                        np.array([x, y, level.elevation]), level.restraint)
                    level.nodes_primary.add(top_node)
                # check to see if bottom node exists
                bot_node = level.previous_lvl.look_for_node(
                    x, y)
                # create it if it does not exist
                if not bot_node:
                    bot_node = Node(
                        np.array([x, y, level.previous_lvl.elevation]),
                        level.previous_lvl.restraint)
                    level.previous_lvl.nodes_primary.add(bot_node)
                # add the column connecting the two nodes
                column = BeamColumn(
                    node_i=top_node,
                    node_j=bot_node,
                    ang=self.active_angle,
                    section=self.sections.active,
                    n_sub=n_sub,
                    placement=self.active_placement,
                    offset_i=np.array((0., 0., 0.)).copy(),
                    offset_j=np.array((0., 0., 0.)).copy()
                )
                columns.append(column)
                level.columns.add(column)
        return columns

    def add_beam_at_points(self,
                           start: np.ndarray,
                           end: np.ndarray,
                           n_sub=1,
                           offset_i=np.zeros(shape=3).copy(),
                           offset_j=np.zeros(shape=3).copy()):
        """
        Adds a beam connecting the given points
        at all the active levels.
        Existing nodes are used, otherwise they are created.
        Args:
            start (np.ndarray): X,Y coordinates of point i
            end (np.ndarray): X,Y coordinates of point j
            n_sub (int): Number of internal elements to add
            offset_i (np.ndarray): X,Z,Y components of a
                      vector that starts at node i and goes
                      to the internal end of the rigid offset
                      of the i-side of the beam.
            offset_j ~ similar to offset i, for the j side.
        Returns:
            beams (list[BeamColumn]): added beams.
        """
        if not self.sections.active:
            raise ValueError("No active section specified")
        beams = []
        for level in self.levels.active:
            # check to see if start node exists
            start_node = level.look_for_node(*start)
            # create it if it does not exist
            if not start_node:
                start_node = Node(
                    np.array([*start, level.elevation]), level.restraint)
                level.nodes_primary.add(start_node)
            # check to see if end node exists
            end_node = level.look_for_node(*end)
            # create it if it does not exist
            if not end_node:
                end_node = Node(
                    np.array([*end, level.elevation]), level.restraint)
                level.nodes_primary.add(end_node)
            # add the beam connecting the two nodes
            # avoid making a reference to the same arrays for all beams
            beam = BeamColumn(node_i=start_node,
                              node_j=end_node,
                              ang=self.active_angle,
                              section=self.sections.active,
                              n_sub=n_sub,
                              placement=self.active_placement,
                              offset_i=offset_i,
                              offset_j=offset_j)
            level.beams.add(beam)
            beams.append(beam)
        return beams

    def add_columns_from_grids(self, n_sub=1):
        """
        Uses the currently defined gridsystem to obtain all locations
        where gridlines intersect, and places a column on
        all such locations.
        Args:
            n_sub (int): Number of internal elements to add.
        Returns:
            columns (list[BeamColumn]): added columns
        """
        isect_pts = self.gridsystem.intersection_points()
        columns = []
        for pt in isect_pts:
            cols = self.add_column_at_point(
                *pt,
                n_sub=n_sub)
            columns.extend(cols)
        return columns

    def add_beams_from_grids(self, n_sub=1):
        """
        Uses the currently defined gridsystem to obtain all locations
        where gridlines intersect. For each gridline, beams are placed
        connecting all the intersection locations of that
        gridline with all other gridlines.
        Args:
            n_sub (int): Number of internal elements to add
        """
        beams = []
        for grid in self.gridsystem.grids:
            isect_pts = self.gridsystem.intersect(grid)
            for i in range(len(isect_pts)-1):
                bms = self.add_beam_at_points(
                    isect_pts[i],
                    isect_pts[i+1],
                    n_sub=n_sub)
                beams.extend(bms)
        return beams

    #############################################
    # Remove methods - remove objects           #
    #############################################

    def clear_gridlines(self):
        self.gridsystem.grids = []

    #############################################
    # Set active methods - alter active objects #
    #############################################

    def set_active_levels(self, names: list[str]):
        """
        Sets the active levels of the building.
        An empty `names` list is interpreted as
        activating all levels.
        """
        self.levels.set_active(names)

    def set_active_groups(self, names: list[str]):
        """
        Sets the active groups of the building.
        """
        self.groups.set_active(names)

    def set_active_material(self, name: str):
        """
        Sets the active material.
        """
        self.materials.set_active(name)

    def set_active_section(self, name: str):
        """
        Sets the active section.
        """
        self.sections.set_active(name)

    def set_active_placement(self, placement: str):
        """
        Sets the active placement
        """
        self.active_placement = placement

    def set_active_angle(self, ang: float):
        """
        Sets the active angle
        """
        self.active_angle = ang

    ############################
    # Methods for adding loads #
    ############################

    def assign_surface_DL(self,
                          load_per_area: float):
        """
        Assigns surface loads on the active levels
        """
        for level in self.levels.active:
            level.assign_surface_DL(load_per_area)

    #########################
    # Preprocessing methods #
    #########################

    def list_of_beams(self):
        list_of_beams = []
        for lvl in self.levels.level_list:
            for beam in lvl.beams.element_list:
                list_of_beams.append(beam)
        return list_of_beams

    def list_of_columns(self):
        list_of_columns = []
        for lvl in self.levels.level_list:
            for col in lvl.columns.element_list:
                list_of_columns.append(col)
        return list_of_columns

    def list_of_beamcolumn_elems(self):
        list_of_frames = []
        for element in self.list_of_beams() + self.list_of_columns():
            list_of_frames.append(element)
        return list_of_frames

    def list_of_internal_elems(self):
        beamcolumn_elems = self.list_of_beamcolumn_elems()
        result = []
        for element in beamcolumn_elems:
            result.extend(element.internal_elems)
        return result

    def list_of_primary_nodes(self):
        list_of_nodes = []
        for lvl in self.levels.level_list:
            for node in lvl.nodes_primary.node_list:
                list_of_nodes.append(node)
        return list_of_nodes

    def list_of_parent_nodes(self):
        list_of_parent_nodes = []
        for lvl in self.levels.level_list:
            if lvl.parent_node:
                list_of_parent_nodes.append(lvl.parent_node)
        return list_of_parent_nodes

    def list_of_internal_nodes(self):
        list_of_internal_nodes = []
        frame_elems = self.list_of_beamcolumn_elems()
        for frame in frame_elems:
            list_of_internal_nodes.extend(frame.internal_nodes)
        return list_of_internal_nodes

    def list_of_all_nodes(self):
        return self.list_of_primary_nodes() + \
            self.list_of_internal_nodes() + \
            self.list_of_parent_nodes()

    def list_of_connections(self):
        bc_elems = self.list_of_beamcolumn_elems()
        result = []
        for elm in bc_elems:
            result.extend([elm.connection_i,
                           elm.connection_j])
        return result

    def retrieve_beam(self, uniq_id: int) -> BeamColumn:
        beams = self.list_of_beams()
        result = None
        for beam in beams:
            if beam.uniq_id == uniq_id:
                result = beam
                break
        return result

    def retrieve_column(self, uniq_id: int) -> BeamColumn:
        columns = self.list_of_columns()
        result = None
        for col in columns:
            if col.uniq_id == uniq_id:
                result = col
                break
        return result

    def reference_length(self):
        """
        Returns the largest dimension of the
        bounding box of the building
        (used in graphics)
        """
        p_min = np.full(3, np.inf)
        p_max = np.full(3, -np.inf)
        for node in self.list_of_primary_nodes():
            p = np.array(node.coords)
            p_min = np.minimum(p_min, p)
            p_max = np.maximum(p_max, p)
        ref_len = np.max(p_max - p_min)
        return ref_len

    def preprocess(self, assume_floor_slabs=True, self_weight=True):
        """
        Preprocess the building. No further editing beyond this point.
        This method initiates automated calculations to
        get things ready for running an analysis.
        """
        def apply_floor_load(lvl):
            """
            Given a building level, distribute
            the surface load of the level on the beams
            of that level.
            """
            if lvl.floor_coordinates is not None:
                for beam in lvl.beams.element_list:
                    udlZ_val = - beam.tributary_area * \
                        lvl.surface_DL / beam.length_clear()
                    beam.add_udl_glob(
                        np.array([0.00, 0.00, udlZ_val]),
                        ltype='floor')
                for node in lvl.nodes_primary.node_list:
                    pZ_val = - node.tributary_area * \
                        lvl.surface_DL
                    node.load_fl += np.array((0.00, 0.00, -pZ_val,
                                              0.00, 0.00, 0.00))
        # ~~~

        for lvl in self.levels.level_list:
            if lvl.parent_node:
                # remove parent nodes
                del(lvl.parent_node)
                # floor-associated level parameters
                del(lvl.floor_bisector_lines)
                del(lvl.floor_coordinates)
                # zero-out floor load/mass contribution
                for node in lvl.list_of_primary_nodes():
                    node.load_fl = np.zeros(6)
                for elm in lvl.list_of_internal_elems():
                    for ielm in elm.internal_elems:
                        ielm.udl_fl = np.zeros(3)

        for lvl in self.levels.level_list:
            if lvl.restraint != "free":
                continue
            if assume_floor_slabs:
                beams = lvl.beams.element_list
                coords, bisectors = \
                    trib_area_analysis.calculate_tributary_areas(
                        beams)
                lvl.floor_coordinates = coords
                lvl.floor_bisector_lines = bisectors
                # distribute floor loads on beams and nodes
                apply_floor_load(lvl)

        # frame element self-weight
        if self_weight:
            for elm in self.list_of_beamcolumn_elems():
                elm.apply_self_weight_and_mass(1.00)
        if assume_floor_slabs:
            for lvl in self.levels.level_list:
                # accumulate all the mass at the parent nodes
                if lvl.restraint != "free":
                    continue
                properties = mesher.geometric_properties(lvl.floor_coordinates)
                floor_mass = -lvl.surface_DL * \
                    properties['area'] / common.G_CONST
                assert(floor_mass >= 0.00),\
                    "Error: floor area properties\n" + \
                    "Overall floor area should be negative (by convention)."
                floor_centroid = properties['centroid']
                floor_mass_inertia = properties['inertia']['ir_mass']\
                    * floor_mass
                self_mass_centroid = np.array([0.00, 0.00])  # excluding floor
                total_self_mass = 0.00
                for node in lvl.list_of_all_nodes():
                    self_mass_centroid += node.coords[0:2] * node.mass[0]
                    total_self_mass += node.mass[0]
                self_mass_centroid = self_mass_centroid * \
                    (1.00/total_self_mass)
                total_mass = total_self_mass + floor_mass
                # combined
                centroid = [
                    (self_mass_centroid[0] * total_self_mass +
                     floor_centroid[0] * floor_mass) / total_mass,
                    (self_mass_centroid[1] * total_self_mass +
                     floor_centroid[1] * floor_mass) / total_mass
                ]
                lvl.parent_node = Node(
                    np.array([centroid[0], centroid[1],
                              lvl.elevation]), "parent")
                lvl.parent_node.mass = np.array([total_mass,
                                                 total_mass,
                                                 0.,
                                                 0., 0., 0.])
                lvl.parent_node.mass[5] = floor_mass_inertia
                for node in lvl.list_of_all_nodes():
                    lvl.parent_node.mass[5] += node.mass[0] * \
                        np.linalg.norm(lvl.parent_node.coords - node.coords)**2
                    node.mass[0] = 0.
                    node.mass[1] = 0.

    def level_masses(self):
        lvls = self.levels.level_list
        n_lvls = len(lvls)
        level_masses = np.full(n_lvls, 0.00)
        for i, lvl in enumerate(lvls):
            total_mass = 0.00
            for node in lvl.list_of_all_nodes():
                if node.restraint_type == "free":
                    total_mass += node.mass[0]
            if lvl.parent_node:
                total_mass += lvl.parent_node.mass[0]
            level_masses[i] = total_mass
        return level_masses

    ###############################
    # Preprocessing Visualization #
    ###############################

    def plot_building_geometry(self, extrude_frames=False):
        preprocessing_3D.plot_building_geometry(
            self, extrude_frames=extrude_frames)

    def plot_2D_level_geometry(self,
                               lvlname: str,
                               extrude_frames=False):
        preprocessing_2D.plot_2D_level_geometry(
            self,
            lvlname,
            extrude_frames=extrude_frames)
