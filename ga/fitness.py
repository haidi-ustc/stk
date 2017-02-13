"""
Module for defining fitness functions.

Extending MMEA: Adding fitness functions
----------------------------------------
To add a new fitness function simply write it as a function in this
module. It will need to take the ``MacroMolecule`` instance as its first
argument and this argument should be called ``macro_mol``. The purpose
of this is to help users identify which arguments are handled
automatically by MMEA and which they need to define in the input file.
The convention is that if the fitness function takes an argument called
``macro_mol`` they do not have to specify that argument in the input 
file. 

In order for fitness functions to be parallelizable, a requirement must
be met. The fitness function's return value should be the `macro_mol` 
instance they took as an argument.

The simplest fitness functions will only need to assign a value to the 
`fitness` attribute of the `macro_mol` and set its `fitness_fail` 
attribute to ``False`` if that assignment was successful. The value
calculated for `fitness` must be between 0 (exclusive) and infinity.  

More complicated fitness functions can be designed by assigning to the
`unscaled_fitness` attribute of `macro_mol`. In these cases, the 
fitness function assignes to the `unscaled_fitness` attribute. This
value is then used by a normalization function to calculate the fitness
value. Note that in these cases the normalization function assignes to
the `fitness` attribute, not the fitness function itself. 

In these cases any value or object can be placed in `unscaled_fitness`,
as long as normalization function which knows how to convert that data
into a fitness value is used together with the fitness function.

A fitness function may be complex and may not fit neatly into a single 
function. For example, the ``cage_target()`` fitness function needs to 
call ``_generate_complexes()`` in order to sample various conformations
before outputting a fitness value. This is fine. Define helper functions 
such as ``_generate_complexes()`` within this module but make sure they 
are private. This means that names of helper functions begin with a 
leading underscore. 

A note on plotting.
-------------------
As mentioned before some fitness functions may be complex and as a
result manipulate all sorts of data. Typically, in order to measure the
progress of a GA, the fitness values in the population are tracked 
across generations. However, let's say that some hypothetical fitness 
function also calculates the energies of molecules. It may be quite 
interesting plot the evolution of energies across generations too. If 
this is the case the fitness function may assign to the 
`progress_params` attribute of `macro_mol`: 

    macro_mol.progress_params = [mol_energy]
    
Now a plot showing the change in `mol_energy` across generations will be 
made too, along with the plot showing the changes in fitness.

What if two things are needed to be kept track of?

    macro_mol.progress_params = [mol_energy, mol_radius]
    
Great, now a progress plot for each of the variables will be made.

How will the y axes be labelled in each plot?
The decorator `_param_labels()` exists for this.

Let's create a basic outline of a some fitness function:

    @_param_labels('Molecule Energy / J mol-1', 'Molecule Radius / m-9')
    def this_is_the_fitness_function(macro_mol, some_param):
        ...
        calculate_stuff()
        ...
        macro_mol.progress_params = [mol_energy, mol_radius]
        ...
        macro_mol.fitness_fail = False
        return macro_mol

If this function is used in the GA, a progress plot will be made for 
each of the `progress_params` and they will have their y-axes labelled
'Molecule Energy / J mol-1' and 'Molecule Radius / m-9', respectively.

"""

import numpy as np
import rdkit.Chem as chem
import copy
from functools import partial, wraps
import networkx as nx
import multiprocessing as mp
import warnings
from collections import Counter

from ..convenience_tools import (matrix_centroid,
                                 FunctionData, MolError, 
                                 rotation_matrix_arbitrary_axis)
                                
from ..molecular import (MacroMolecule, 
                               StructUnit, Energy, optimization)

def _calc_fitness(func_data, population):
    """
    Calculates the fitness values of all members of a population.   
    
    Parameters
    ----------
    func_data : FunctionData
        A ``FunctionData`` instance representing the chosen fitness 
        function and any additional parameters it may require.
    
    population : Population
        The population whose members must have their fitness calculated.
        
    Returns
    -------
    list
        The members of `population` which have had their fitness
        calculated.
    
    """

    # Get the fitness function object.
    func = globals()[func_data.name]
    # Make sure it won't raise errors while using multiprocessing.
    p_func = _FitnessFunc(partial(func, **func_data.params))

    # Apply the function to every member of the population, in parallel.
    with mp.get_context('spawn').Pool() as pool:
        evaluated = pool.map(p_func, population)

        # Make sure the cache is updated with the evaluated versions.
        for member in evaluated:
            member.update_cache()
        
    return evaluated

def _calc_fitness_serial(func_data, population):
    """
    Calculates the fitness values of all members of a population.  
    
    Parameters
    ----------
    func_data : FunctionData
        A ``FunctionData`` instance representing the chosen fitness 
        function and any additional parameters it may require.
    
    population : Population
        The population whose members must have their fitness calculated.
        
    Returns
    -------
    None : NoneType    
    
    """

    # Get the fitness function object.
    func = globals()[func_data.name]
    

    # Apply the function to every member of the population.
    for macro_mol in population:
        _FitnessFunc(func(macro_mol, **func_data.params))


def _param_labels(*labels):
    """
    Adds the `param_labels` attribute to a fitness function.
    
    The point of this decorator is described in the module level
    docstring.
    
    Parameters
    ----------
    labels : tuple
        List of strings about the fitness labels used for plotting EPPs.
        The order of the strings should represent the order of the
        fitness ``vars`` in the fitness funciton. In practice it should
        correspond to the order of the ``coeffs`` or ``exponents`` 
        parameters given to the fitness function.
    
    Returns
    -------
    func
        Decorated function.
        
    """
    
    def add_labels(func):
        func.param_labels = labels    
        return func
        
    return add_labels

class _FitnessFunc:
    """
    A decorator for fitness functions.
    
    This decorator is applied to all fitness functions automatically in
    _calc_fitness(). It should not be applied explicitly when defining
    the functions.
    
    The decorator prevents fitness functions from raising if 
    they fail (necessary for multiprocessing) and prevents them from
    being run twice on the same molecule.    
    
    Attributes
    ----------
    func : function
        The fitness function which is to be prevented from raising and
        running twice on the same molecule.    
    
    """
    
    def __init__(self, func):
        self.func = func
        wraps(func)(self)
    
    def __call__(self, macro_mol, *args,  **kwargs):
        try:
            if macro_mol.fitness or macro_mol.unscaled_fitness:
                print('Skipping {0}'.format(macro_mol.file))
                return macro_mol          
            return self.func(macro_mol, *args, **kwargs)
            
        except Exception as ex:
            # Prevents the error from being raised, but records it in 
            # ``failures.txt``.
            macro_mol.fail()
            MolError(ex, macro_mol, "During fitness calculation")
            return macro_mol
            
def random_fitness(macro_mol):
    """
    Returns a random fitness value between 1 and 10.

    Parameters
    ----------
    macro_mol : MacroMolecule
        The macromolecule to which a fitness value is to be assigned.
    
    Modifies
    --------
    macro_mol.fitness : float
        Assigns a fitness to this attribute.
    
    Returns
    -------
    macro_mol : MacroMolecule
        The `macro_mol` with an integer between 0 (including) and 100 
        (excluding) as its fitness.

    """
    
    macro_mol.fitness = abs(np.random.normal(50,20))
    return macro_mol    

@_param_labels('carrot_var1', 'carrot_var2', 'stick_var1', 'stick_var2')
def random_fitness_tuple(macro_mol):
    """
    Returns a tuple holding 2 arrays with random values.
    
    To be used a random fitness function when testing out the
    carrots_and_sticks() normalization function.
    
    Parameters
    ----------
    macro_mol : MacroMolecule
        The macromolecule which is to have its fitness calculated.
        
    Modifies
    --------
    macro_mol.fitness_fail : bool
        Set to ``False``.
        
    macro_mol.unscaled_fitness : numpy.array
        The tuple of random arrays is placed in this attribute.
        
    macro_mol.progress_params : list
        The values of the random arrays are placed in this attribute 
        for use by the ``Plotter`` class.
        
    Returns
    -------
    macro_mol : MacroMolecule
        The `macro_mol` with a tuple of 2 random arrays as its fitness
        value.
        
    """
      
    carrot_array = abs(np.random.normal(50,20,2))
    stick_array = abs(np.random.normal(50,20,2))
    macro_mol.unscaled_fitness = (carrot_array, stick_array)
    macro_mol.fitness_fail = False
    macro_mol.progress_params = [*carrot_array, *stick_array]
    return macro_mol

def raiser(macro_mol, param1, param2=2):
    """
    Doens't calculate a fitness value, raises an error instead.
    
    This function is used to test that when fitness functions raise
    errors during multiprocessing, they are handled correctly.

    Parameters
    ---------    
    param1 : object
        Dummy parameter, does nothing.
        
    param2 : object (default = 2)
        Dummy keyword parameter, does nothing.
        
    Returns
    -------
    This function does not return. It only raises.
    
    Raises
    ------
    Exception
        An exception is always raised.
    
    """
    
    raise Exception('Raiser fitness function used.')

    
# Provides labels for the progress plotter.
@_param_labels('Cavity Difference ','Window Difference ',
                'Asymmetry ', 'Positive Energy per Bond ', 
                'Negative Energy per Bond ')
def cage(macro_mol, target_cavity, target_window=None, 
         pseudoformation_params=
         { 'energy_func' : FunctionData('rdkit', forcefield='uff') }):
    """
    Calculates the fitness of a cage.
    
    This function is intended to be used with the normalization function
    ``carrots_and_sticks()`` defined in ``normalization.py``.

    The fitness function creates a tuple of 2 arrays. The first array
    holds parameters of `macro_mol` which contribute to a high fitness.
    The second array holds parameters of `macro_mol` which cause a low
    fitness.

    The parameter which indicates high fitness is the negative formation 
    energy per bond made. The remaining parameters indicate low fitness
    and are:
        1) `cavity_diff` - the difference between the cavity of 
           `macro_mol` and the `target_cavity`.
        2) `window_diff` - the difference between the largest window of
           `macro_mol` and `target_window`.
        3) `asymmetry` - the sum of the size differences of all the
           windows in `macro_mol`.
        4) `pos_eng_per_bond` - The postive formation energy of 
           `macro_mol` per bond made.
  
    Parameters
    ----------
    macro_mol : Cage
        The cage whose fitness is to be calculated.
        
    target_cavity : float
        The desired diameter of the cage's pore.

    target_window : float (default = None)
        The desired diameter of the largest window of the cage. If 
        ``None`` then `target_cavity` is used.
        
    pseudoformation_params : dict (default = 
            { 'energy_func' : FunctionData('rdkit', forcefield='uff') })
                                
        This fitness function calculates the formation energy using the
        ``Energy.pseudoformation()`` method. This parameter defines the
        arguments passed to this method via a dictionary. The name of 
        the argument is the key and the value of the argument is the
        value.
        
        Default initialized arguments of Energy.pseudoformation() only 
        need to be specified in `energy_params` if the user wishes to
        change the default value.
        
        To see what arguments the `Energy.pseudoformation()` method
        requires, try using the  `-h` option:

            python -m mmea -h energy
    
    Modifies
    --------
    macro_mol.fitness_fail : bool
        The function sets this to ``True`` if one of the parameters 
        was not calculated. ``False`` if every parameter was calculated
        successfully.
        
    macro_mol.unscaled_fitness : tuple of 2 numpy.arrays
        The first numpy array holds the value of the negative energy
        per bond made. The second array holds the remaining parameters
        described above.   
        
    macro_mol.progress_params : list
        Places the calculated parameters in a single list. The order
        corresponds to the arguments in the ``_param_labels()`` 
        decorator applied to this function.
        
    Returns
    -------
    macro_mol : Cage
        The `macro_mol` with its fitness parameters calculated.

    """

    # Prevents warnings from getting printed when using multiprocessing.
    warnings.filterwarnings('ignore')
                   
    if target_window is None:
        target_window = target_cavity                       
                   
    cavity_diff = abs(target_cavity - 
                      macro_mol.topology.cavity_size())

    if macro_mol.topology.windows is not None:
        window_diff = abs(target_window - 
                          max(macro_mol.topology.windows))
    else:
        window_diff  = None
        
    if  macro_mol.topology.window_difference() is not None:             
        asymmetry = macro_mol.topology.window_difference()
    else:
        asymmetry = None

    print('\n\nCalculating complex energies.\n')    
    e_per_bond = macro_mol.energy.pseudoformation(
                                           **pseudoformation_params)
    e_per_bond /= macro_mol.topology.bonds_made

    if e_per_bond < 0:
        ne_per_bond = abs(e_per_bond)
        pe_per_bond = 0
    else:
        ne_per_bond = 0
        pe_per_bond = e_per_bond
    
    macro_mol.progress_params = [cavity_diff, window_diff, 
                               asymmetry, pe_per_bond, -ne_per_bond]  
                    
    macro_mol.fitness_fail = (True if None in 
                              macro_mol.progress_params else False)

    macro_mol.unscaled_fitness = (np.array([ne_per_bond]),
                    np.array([cavity_diff,
                    (window_diff if window_diff is not None else 0),
                    (asymmetry if asymmetry is not None else 0),
                    pe_per_bond]))
    
    return macro_mol

@_param_labels('Negative Binding Energy', 'Positive Binding Energy', 
               'Asymmetry') 
def cage_target(macro_mol, target_mol_file, macromodel_path, 
                rotations=0, md=False):
    """
    Calculates the fitness of a cage / target complex.
    
    This function should be used with the ``carrots_and_sticks()``
    normalization function.    
    
    The function calculates the binding energy of the cage/target 
    complex and the asymmetry of the molecule. It creates a tuple:
    
        macro_mol.unscaled_fitness = (numpy.array([neg_binding_eng],
                               numpy.array([pos_binding_eng, asymmetry])
    
    Parameters
    ----------
    macro_mol : Cage
        The cage which is to have its fitness calculated,

    target_mol_file : str
        The full path of the .mol file hodling the target molecule
        placed inside the cage.
        
    macromodel_path : str
        The Schrodinger directory path.

    rotations : int (default = 0)
        The number of times the target should be randomly rotated within 
        the cage cavity in order to find the most stable conformation.
        
    md : bool (default = False)
        Toggles the running of MD on cage-target complexes.
        
    Modifies
    --------
    macro_mol.progress_params : list
        Places the various physical properties of `macro_mol` which 
        contribute to fitness in this attribute. This is used for
        plotting the EPP and other stats.        

    macro_mol.fitness_fail : bool    
        This attribute is set to ``True`` if the fitness function
        completes successfully.  Otherwise set to ``False``.
    
    macro_mol.unscaled_fitness : tuple of numpy.arrays
        Places the unscaled fitness parameters into this attribute.
        The parameters which increase with fitness are placed in the
        first element of the tuple while the parameters which decrease
        with increased fitness are placed in the second element.

    Returns
    -------
    macro_mol
        The `macro_mol` with its unscaled fitness parameters calculated.
    
    """

    return _cage_target(macro_mol, target_mol_file, macromodel_path,
                        _generate_complexes, rotations+1, md=md)

@_param_labels('Negative Binding Energy', 'Positive Binding Energy', 
               'Asymmetry') 
def cage_c60(macro_mol, target_mol_file, 
             macromodel_path, n5fold, n2fold, md=False):
    """
    Calculates the fitness of a cage / C60 complex.
    
    The difference between this function and `cage_target()` is that
    the rotations are specifically aimed at sampling C60 entirely and
    systematically. Rather than the random sampling of the other
    function.
    
    This function should be used in together with the 
    ``carrots_and_sticks()`` normalization function.
  
    The function calculates the binding energy of the cage/target 
    complex and the asymmetry of the molecule. It creates a tuple:
    
        macro_mol.unscaled_fitness = (numpy.array([neg_binding_eng],
                               numpy.array([pos_binding_eng, asymmetry])
  
    Parameters
    ----------
    macro_mol : Cage
        The cage which is to have its fitness calculated.

    target_mol_file : str
        The full path of the .mol file hodling the target molecule
        placed inside the cage.
        
    macromodel_path : str
        The Schrodinger directory path.

    n5fold : int
        The number of rotations along the 5-fold axis of symmetry.
        
    n2fold : int
        The number of rotations along the 2 fold axis of symmetry per
        rotation along the 5-fold axis.

    md : bool (default = False)
        If ``True`` the generated complexes will have a MD simulation
        performed on them to find the lowest energy conformer.

    Modifies
    --------
    macro_mol.progress_params : list
        Places the various physical properties of `macro_mol` which 
        contribute to fitness in this attribute. This is used for
        plotting the EPP and other stats.        

    macro_mol.fitness_fail : bool    
        This attribute is set to ``True`` if the fitness function
        completes successfully.  Otherwise set to ``False``.
    
    macro_mol.unscaled_fitness : tuple of numpy.arrays
        Places the unscaled fitness parameters into this attribute.
        The parameters which increase with fitness are placed in the
        first element of the tuple while the parameters which decrease
        with increased fitness are placed in the second element.

    Returns
    -------
    macro_mol
        The `macro_mol` with its unscaled fitness parameters calculated.
    
    """
    return _cage_target(macro_mol, target_mol_file, macromodel_path,
                        _c60_rotations, n5fold, n2fold, md=md)


def _cage_target(macro_mol, target_mol_file, macromodel_path, 
                 rotation_func, *rot_args, md=False):
    """
    A general fitness function for calculting fitness of complexes.

    This function should be inherited by other fitness functions which
    defined their own rotation function. For example ``cage_c60()`` and
    ``cage_target()``.
    
    This function is meant to be used with the ``carrots_and_sticks()``
    normalization function in ``normalization.py``.
    
    Parameters
    ----------
    macro_mol : Cage
        The cage which is to have its fitness calculated.

    target_mol_file : str
        The full path of the .mol file hodling the target molecule
        placed inside the cage.
        
    macromodel_path : str
        The Schrodinger directory path.
        
    rotation_func : function
        A generator which carries out the rotations of the target within
        the cage. It yields the complexes.

    *rot_args : tuple
        Parameters to be passed to `rotation_func`.
        
    md : bool (default = False)
        If ``True`` the generated complexes will have a MD simulation
        performed on them to find the lowest energy conformer.

    Modifies
    --------
    macro_mol.progress_params : list
        Places the various physical properties of `macro_mol` which 
        contribute to fitness in this attribute. This is used for
        plotting the EPP and other stats.        

    macro_mol.fitness_fail : bool    
        This attribute is set to ``True`` if the fitness function
        completes successfully.  Otherwise set to ``False``.
    
    macro_mol.unscaled_fitness : tuple of numpy.arrays
        Places the unscaled fitness parameters into this attribute.
        The parameters which increase with fitness are placed in the
        first element of the tuple while the parameters which decrease
        with increased fitness are placed in the second element.

    Returns
    -------
    macro_mol
        The `macro_mol` with its unscaled fitness parameters calculated.
    
    """
                     
    warnings.filterwarnings('ignore')
       
    # Make a copy version of `macro_mol` which is unoptimizted.
    unopt_macro_mol = copy.deepcopy(macro_mol)
    unopt_macro_mol.topology.build()
    
    
    # Create an instance of the target molecule as a ``StructUnit``.
    target = StructUnit(target_mol_file)        

    # This function creates a new molecule holding both the target
    # and the cage centered at the origin. It then calculates the 
    # energy of this complex and compares it to the energies of the
    # molecules when separate. The more stable the complex relative
    # to the individuals the higher the fitness.
    
    # Create rdkit instances of the target in the cage for each
    # rotation.        
    rdkit_complexes = rotation_func(unopt_macro_mol, target, 
                                     *rot_args)

    # Optimize the strcuture of the cage/target complexes.
    macromol_complexes = []        
    for i, complex_ in enumerate(rdkit_complexes):
        # In order to use the optimization functions, first the data 
        # is loaded into a ``MacroMolecule`` instance and its .mol 
        # file is written to the disk.
        mm_complex = MacroMolecule.__new__(MacroMolecule)
        mm_complex.mol = complex_
        mm_complex.file = macro_mol.file.replace(
                            '.mol', '_COMPLEX_{0}.mol'.format(i))
        mm_complex.write()
        mm_complex.optimized = False
        mm_complex.energy = Energy(mm_complex)
        optimization.macromodel_opt(mm_complex, no_fix=True,
                       macromodel_path=macromodel_path, md=md)
        macromol_complexes.append(mm_complex)

    # Calculate the energy of the complex and compare to the
    # individual energies. If more than complex was made, use the
    # most stable version.
    energy_separate = (
            macro_mol.energy.macromodel(16, macromodel_path) + 
            target.energy.macromodel(16, macromodel_path))
    
    print('\n\nCalculating complex energies.\n')
    min_eng_cmplx = min(macromol_complexes, 
                key=lambda x : 
                    x.energy.macromodel(16, macromodel_path))                        

    binding_energy = (
                min_eng_cmplx.energy.values[
    FunctionData('macromodel', forcefield=16)] - energy_separate)
        
    if binding_energy > 0:
        pos_be = binding_energy
        neg_be = 0
    else:
        pos_be = 0
        neg_be = abs(binding_energy)

    frag1, frag2 = chem.GetMolFrags(min_eng_cmplx.mol, 
                                    asMols=True,
                                    sanitizeFrags=False)
                                  
    cage_counter = Counter(x.GetAtomicNum() for x in 
                            macro_mol.mol.GetAtoms())
    frag_counters = [(frag1, Counter(x.GetAtomicNum() for x in 
                            frag1.GetAtoms())),

                    (frag2, Counter(x.GetAtomicNum() for x in 
                            frag2.GetAtoms()))]

    cmplx_cage_mol = next(frag for frag, counter in frag_counters if 
                        counter == cage_counter)
    
    cmplx_cage = MacroMolecule.__new__(MacroMolecule)
    cmplx_cage.mol = cmplx_cage_mol
    cmplx_cage.topology = type(macro_mol.topology)(cmplx_cage)
    cmplx_cage.file = macro_mol.file.replace(
                     '.mol', '_COMPLEX_{0}_no_target.mol'.format(i))
    cmplx_cage.write()
    

    if cmplx_cage.topology.window_difference() is not None:             
        asymmetry = macro_mol.topology.window_difference()
    else:
        asymmetry = None        

    
    macro_mol.progress_params = [-neg_be, pos_be, asymmetry]        
    
    macro_mol.fitness_fail = (True if None in 
                               macro_mol.progress_params else False)

    macro_mol.unscaled_fitness = (
                      np.array([neg_be]),
                      np.array([pos_be, 
                      (asymmetry if asymmetry is not None else 0)]))

    return macro_mol    

def _generate_complexes(macro_mol, target, number=1):
    """
    Yields rdkit instances of cage / target complexes.
    
    If multiple complexes are returned, they will be different via a
    random rotation accross the x, y and z axes.
    
    Parameters
    ----------
    macro_mol : Cage
        The cage used to form the complex.
        
    target : StructUnit
        The target used to form the complex.
        
    number : int (default = 1)
        The number of complexes to be returned.
        
    Yields
    ------
    rdkit.Chem.rdchem.Mol
        An rdkit instance holding the cage / target complex. 
    
    """

    # First place both the target and cage at the origin.
    macro_mol.set_position([0,0,0])
    target.set_position([0,0,0])
    
    # Get the position matrix of the target molecule.        
    og_pos_mat = target.position_matrix()
    
    # Carry out every rotation and yield a complex for each case.
    for i in range(number):
        rot_target = copy.deepcopy(target)
        
        rot1 = np.random.rand() * 2*np.pi
        rot2 = np.random.rand() * 2*np.pi
        rot3 = np.random.rand() * 2*np.pi
        
        rot_mat1 = rotation_matrix_arbitrary_axis(rot1, [1,0,0])
        rot_mat2 = rotation_matrix_arbitrary_axis(rot2, [0,1,0])
        rot_mat3 = rotation_matrix_arbitrary_axis(rot3, [0,0,1])
        
        new_pos_mat = np.dot(rot_mat1, og_pos_mat)
        new_pos_mat = np.dot(rot_mat2, new_pos_mat)
        new_pos_mat = np.dot(rot_mat3, new_pos_mat)
        
        rot_target.set_position_from_matrix(new_pos_mat)
        
        yield chem.CombineMols(macro_mol.mol, rot_target.mol)
    
def _c60_rotations(macro_mol, c60, n5fold, n2fold):
    """
    Rotates C60 about its axes of symmetry and places it in `macro_mol`.
    
    Parameters
    ----------
    macro_mol : MacroMolecule
        The cage which should have C60 placed inside it.
        
    c60 : StructUnit
        A StructUnit instance of C60.
        
    n5fold : int
        The number of rotations along the 5-fold axis of symmetry.
        
    n2fold : int
        The number of rotations along the 2 fold axis of symmetry per
        rotation along the 5-fold axis.
        
    Yields
    ------
    rdkit.Chem.rdchem.Mol
        An rdkit instance holding the cage / C60 complex. 
    
    """
    
    
    macro_mol.set_position([0,0,0])
    c60.set_position([0,0,0])
    
    # Step 1: Align the 5 membered ring with the z-axis.
    
    # Find a the ids of atoms in a membered ring.
    g = c60.graph()
    ids = next(x for x in nx.cycle_basis(g) if len(x) == 5)
    # Place the coordinates of those atoms in a matrix.
    ring_matrix = np.matrix([c60.atom_coords(id_) for id_ in ids])

    # Get the centroid of the ring.    
    ring_centroid = matrix_centroid(ring_matrix)
    # Align the centroid of the ring with the z-axis.
    c60.set_orientation(ring_centroid, [0,0,1])
    aligned_c60 = copy.deepcopy(c60)
    
    # Step 2: Get the rotation angles and apply the rotations. Yield 
    # the resulting complex.
    
    # Get the angles of the 5 and 2 fold rotations.
    angles5fold = np.arange(0, 72/180*np.pi, 72/180*np.pi/n5fold)
    angles2fold = np.arange(0, np.pi, np.pi/n2fold)
    
    for angle5 in angles5fold:
        for angle2 in angles2fold:
            buckyball = copy.deepcopy(aligned_c60)
            buckyball.rotate(angle5, [0,0,1])
            buckyball.rotate(angle2, [0,1,0])
            yield chem.CombineMols(macro_mol.mol, buckyball.mol)

    
    
    

    
    
        
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
