import numpy as np

from pylub.eos import EquationOfState
from pylub.geometry import Analytic
from pylub.field import VectorField
from pylub.flux import Flux


class Solver:

    def __init__(self, disc, BC, geometry, numerics, material, q_init):

        self.type = str(geometry['type'])
        self.numFlux = str(numerics['numFlux'])
        self.adaptive = bool(numerics['adaptive'])
        self.dt = float(numerics['dt'])
        self.C = float(numerics['C'])

        self.material = material

        self.time = 0.
        self.eps = 1.

        # Gap height
        self.height = VectorField(disc)

        if self.type == 'journal':
            self.height.field[0] = Analytic(disc, geometry).journalBearing(self.height.xx, self.height.yy, axis=0)
        elif self.type == 'inclined':
            self.height.field[0] = Analytic(disc, geometry).linearSlider(self.height.xx, self.height.yy)
        elif self.type == 'parabolic':
            self.height.field[0] = Analytic(disc, geometry).parabolicSlider(self.height.xx, self.height.yy)
        elif self.type == 'step':
            self.height.field[0] = Analytic(disc, geometry).doubleStep(self.height.xx, self.height.yy, axis=0)

        self.height.getGradients()

        rho0 = float(material['rho0'])

        self.q = VectorField(disc)

        if q_init is not None:
            self.q.field = q_init
        else:
            self.q.field[0] = rho0

        if self.type == 'droplet':
            self.q.fill_circle(1.05 * rho0, 0, radius=1e-7)
        elif self.type == 'wavefront':
            self.q.fill_line(1.05 * rho0, 0, 0)

        self.Flux = Flux(disc, BC, geometry, material)

    def solve(self, i):

        if self.adaptive and i > 0:
            self.dt = self.C * min(self.q.dx, self.q.dy) / self.vmax

        p0 = EquationOfState(self.material).isoT_pressure(self.q.field[0])

        if self.numFlux == 'LW':
            self.q = self.Flux.Richtmyer(self.q, self.height, self.dt)

        elif self.numFlux == 'MC':
            self.q = self.Flux.MacCormack(self.q, self.height, self.dt)

        # some scalar output
        self.mass = np.sum(self.q.field[0] * self.height.field[0] * self.q.dx * self.q.dy)
        self.time += self.dt
        self.vSound = EquationOfState(self.material).soundSpeed(self.q.field[0])
        self.vmax = self.vSound + np.amax(np.sqrt(self.q.field[1]**2 + self.q.field[2]**2) / self.q.field[0])

        p1 = EquationOfState(self.material).isoT_pressure(self.q.field[0])

        if i > 0:
            self.eps = np.linalg.norm(p1 - p0) / np.linalg.norm(p0) / self.C
