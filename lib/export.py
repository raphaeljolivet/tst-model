
from typing import Dict
from sympy import parse_expr, Expr, Float
from lca_algebraic import SymDict
from lca_algebraic.base_utils import _method_unit
from lca_algebraic.lca import _preMultiLCAAlgebric, _lambdify
from lca_algebraic.params import _param_registry
from lca_algebraic.stats import _round_expr
from enum import Enum

class ParamType(str, Enum) :
    BOOLEAN = "bool"
    ENUM = "enum"
    FLOAT = "float"

def round_expr(exp_or_dict, num_digits):
    if isinstance(exp_or_dict, dict) :
        return dict({key: _round_expr(val, num_digits) for key, val in exp_or_dict.items()})
    else:
        return _round_expr(exp_or_dict, num_digits)

class FunctionalUnit :

    def __init__(self, quantity, unit):
        self.quantity = quantity
        self.unit = unit

class Lambda:
    """
    This class represents a compiled (lambdified) expression together with the list of requirement parameters and the source expression
    """
    def __init__(self, expr, all_params):

        if isinstance(expr, dict):

            self.lambd = dict()

            # First, gather all expanded parameters
            all_expanded_params = set()
            for key, sub_expr in expr.items():
                expanded_params = list(str(symbol) for symbol in sub_expr.free_symbols)
                all_expanded_params.update(expanded_params)

            all_expanded_params = list(all_expanded_params)

            # Transform them into list of params
            self.params = unexpand_param_names(all_params, all_expanded_params)

            # Lambdify with all expanded params
            for key, sub_expr in expr.items():
                self.lambd[key] = _lambdify(sub_expr, all_expanded_params)

        else:
            if not isinstance(expr, Expr):
                expr = Float(expr)

            expanded_params = list(str(symbol) for symbol in expr.free_symbols)
            self.lambd = _lambdify(expr, expanded_params)
            self.params = unexpand_param_names(all_params, expanded_params)

        self.expr = expr

    def evaluate(self, all_params, param_values):

        # First, set default values
        values = {key: all_params[key].default for key in self.params}

        # Override with actual values
        values.update({key: val for key, val in param_values.items() if key in self.params})

        # Expand
        expanded_values = dict()
        for param_name, val in values.items():
            param = all_params[param_name]
            expanded_values.update(param.expand_values(val))


        if isinstance(self.lambd, dict) :
            return {key: lambd(**expanded_values) for key, lambd in self.lambd.items()}
        else:
            return self.lambd(**expanded_values)


    def __json__(self):

        if isinstance(self.expr, dict):
            expr = {key: str(expr) for key, expr in self.expr.items()}
        else:
            expr = str(self.expr)

        return dict(
            params=self.params,
            expr=expr)

    @classmethod
    def from_json(cls, js, all_params):
        expr = js["expr"]
        if isinstance(expr, dict):
            expr = {key:parse_expr(expr) for key, expr in expr.items()}
        else:
            expr = parse_expr(expr)

        return cls(expr=expr, all_params=all_params)

class Param :

    def __init__(self, name, type, unit, default, values=None):
        self.name = name
        self.type = type
        self.default = default
        self.unit = unit
        if values:
            self.values = values

    @classmethod
    def from_json(cls, js):
        return cls(**js)

    @classmethod
    def from_ParamDef(cls, paramDef):
        return cls(
            name=paramDef.name,
            type=paramDef.type,
            unit=paramDef.unit,
            default=paramDef.default,
            values=getattr(paramDef, "values", None))

    def expand_values(self, value):

        # Simple case
        if self.type != ParamType.ENUM :
            return {self.name:value}

        # Enum ? generate individual boolean param values
        return {"%s_%s" % (self.name, enum): 1 if value == enum else 0 for enum in self.values}

    def expand_names(self):

        if self.type != ParamType.ENUM :
            return [self.name]

        # Enum ? generate individual boolean param values
        return ["%s_%s" % (self.name, enum) for enum in self.values]

    def __json__(self):
        return self.__dict__

def expand_param_names(all_params, param_names):
    res = []
    for param_name in param_names :
        param = all_params[param_name]
        res.extend(param.expand_names())
    return res

def unexpand_param_names(all_params, expanded_param_names):
    """Build a dict of expended_param => param"""
    expanded_params_to_params = {name:param.name for param in all_params.values() for name in param.expand_names() }
    return list(set(expanded_params_to_params[name] for name in expanded_param_names))


class Impact() :
    def __init__(self, name, unit):
        self.name = name,
        self.unit = unit

class Model :

    def __init__(
            self,
            params:Dict,
            expressions:Dict[str, Dict[str, Lambda]],
            functional_units:Dict[str, FunctionalUnit],
            impacts:Dict[str, Impact]) :
        """
        :param params: List of all parameters
        :param expressions: Dict of Dict {axis => {method => Lamba}}
        :param functional_units: Dict of function unit name => formula
        :param impacts : Dict of impacts with their units
        """
        self.params = params
        self.expressions = expressions
        self.functional_units = functional_units
        self.impacts = impacts

    def __json__(self):
        return self.__dict__

    def evaluate(self, impact, functional_unit, axis="total", **param_values):
        """
        :param axis: Axis to consider
        :param impact: Impact to consider
        :param functional_unit: Function unit
        :param param_values: List of parameters
        :return: <Value of impact, or dict of values, in case one axis is used>, <unit>
        """

        if not axis in self.expressions :
            raise Exception("Wrong axis '%s'. Expected one of %s" % (axis, list(self.impacts.keys())))

        expressions_by_impact = self.expressions[axis]

        if not impact in expressions_by_impact:
            raise Exception("Wrong impact '%s'. Expected one of %s" % (impact, list(expressions_by_impact.keys())))

        lambd = expressions_by_impact[impact]
        impact_obj = self.impacts[impact]
        functional_unit = self.functional_units[functional_unit]

        # Compute value of functional unit
        fu_val = functional_unit.quantity.evaluate(self.params, param_values)

        # Compute value of impacts
        impacts = lambd.evaluate(self.params, param_values)

        unit = impact_obj.unit

        if functional_unit.unit is not None :
            unit += "/" + functional_unit.unit

        # Divide the two
        if isinstance(impacts, dict) :
            vals = {key: val / fu_val for key, val in impacts.items()}
        else:
            vals = impacts / fu_val

        return vals, unit


    @classmethod
    def from_json(cls, js) :

        all_params = {key: Param.from_json(val) for key, val in js["params"].items()}

        expressions = {
            axis : {
                method: Lambda.from_json(lambd, all_params)
                for method, lambd in impacts.items()}
            for axis, impacts in js["expressions"].items()}

        functional_units = {
            key: FunctionalUnit(
                quantity=Lambda.from_json(fu["quantity"], all_params),
                unit=fu["unit"])

            for key, fu in js["functional_units"].items()}

        impacts = {key: Impact(impact["name"], impact["unit"]) for key, impact in js["impacts"].items()}

        return cls(all_params, expressions, functional_units, impacts)

def serialize_model(obj) :
    if isinstance(obj, dict) :
        return {key: serialize_model(val) for key, val in obj.items()}

    if hasattr(obj, "__json__") :
        return serialize_model(obj.__json__())

    if hasattr(obj, "__dict__") :
        return serialize_model(obj.__dict__)

    return obj


def export_lca(
        system,
        functional_units : Dict[str, Dict],
        methods_dict,
        axes=None,
        num_digits=3):
    """
    :param system: Root inventory
    :param functional_units : Dict of Dict{unit, quantity}
    :param methods_dict: dict of method_name => method tuple
    :param axes: List of axes
    :param num_digits: Number of digits
    :return: an instance of "Model"
    """

    if axes is None:
        axes = [None]

    # Transform all lca_algebraic parameters to exported ones
    all_params = {param.name: Param.from_ParamDef(param) for param in _param_registry().all()}

    impacts_by_axis = dict()

    for axis in axes :
        print("Processing axis %s" % axis)

        lambdas = _preMultiLCAAlgebric(
            system,
            list(methods_dict.values()),
            axis=axis)

        if axis is None:
            axis = "total"

        # Simplify
        for lambd, method_name  in zip(lambdas, methods_dict.keys()):

            if isinstance(lambd.expr, SymDict):
                lambd.expr = lambd.expr.dict
            lambd.expr = round_expr(lambd.expr, num_digits=num_digits)

        # Save
        impacts_by_axis[axis] = {
            method: Lambda(lambd.expr, all_params)
            for method, lambd in zip(methods_dict.keys(), lambdas)}

    # Dict of functional units
    functional_units = {
        name: FunctionalUnit(
            quantity=Lambda(fu["quantity"], all_params),
            unit=fu["unit"])
        for name, fu in functional_units.items()}

    # Build list of impacts
    impacts = {key: Impact(
        name = str(method),
        unit = _method_unit(method)
    ) for key, method in methods_dict.items()}

    return Model(
        params=all_params,
        functional_units=functional_units,
        expressions=impacts_by_axis,
        impacts=impacts)



