import numpy as np
import multicontact_api
from multicontact_api import ContactPhase, ContactSequence
from mlp.utils.util import createFullbodyStatesFromCS, perturbateContactNormal
from mlp.utils.cs_tools import connectPhaseTrajToFinalState, setInitialFromFinalValues, copyPhaseInitToFinal
from mlp.utils.requirements import Requirements
multicontact_api.switchToNumpyArray()

class CentroidalInputsQuasistatic(Requirements):
    timings = True
    consistentContacts = True
    configurationValues = True

class CentroidalOutputsQuasistatic(CentroidalInputsQuasistatic):
    COMtrajectories = True
    COMvalues = True

## Produce a centroidal trajectory where the CoM only move when the contacts are fixed.
## It is then fixed during the swing phase where one contact is repositionned.
## The position reached by the CoM are given with 2-PAC
## The trajectory of the CoM is a quintic spline with initial and final velocity/acceleration constrained to 0


def getTargetCOMPosition(fullBody, id_state, com_shift_z):
    s_id_init = id_state
    s_id_final = id_state+1
    success = False
    attempts = 10
    while not success and attempts > 0:
        print("call 2-pac for ids : "+str(s_id_init)+ " ; "+str(s_id_final+1))
        tab = fullBody.isReachableFromState(s_id_init, s_id_final, True, False)
        print("tab results : ",tab)
        success = tab[0]
        attempts -= 1
        if not success:
            # add a small perturbation in the contact normals
            print("2-pac failed. Add perturbation and retry")
            s_id_init = perturbateContactNormal(fullBody, id_state)
            assert s_id_init > 0, "Failed to change contact normals."
            s_id_final = perturbateContactNormal(fullBody, id_state+1)
            assert s_id_final > 0, "Failed to change contact normals."
    assert success, "2-pac failed for state id : " + str(id_state)
    if len(tab) == 7:
        cBreak = np.array(tab[1:4]).reshape(-1)
        cCreate = np.array(tab[4:7]).reshape(-1)
        c = (cBreak + cCreate) / 2.
        print("shape c : ",c.shape)
    else:
        c = np.array(tab[1:4]).reshape(-1)
        print("shape c else : ",c.shape)

    print("c before shift : ", c)
    c[2] += com_shift_z
    print("c after  shift : ", c)
    return c



def generate_centroidal_quasistatic(cfg, cs, cs_initGuess=None, fullBody=None, viewer=None, first_iter = True):
    if cs_initGuess:
        print("WARNING : in centroidal.quasiStatic, initial guess is ignored.")
    if not fullBody:
        raise ValueError("quasiStatic called without fullBody object.")
    if not first_iter:
        print("WARNING : in centroidal.quasiStatic, it is useless to iterate several times.")
    beginId, endId = createFullbodyStatesFromCS(cs, fullBody)
    print("beginid = ", beginId)
    print("endId   = ", endId)
    cs_result = ContactSequence(cs)

    # for each phase in the cs, create a corresponding FullBody State and call 2-PAC,
    # then fill the cs struct with a quintic spline connecting the two points found
    id_phase = 0
    for id_state in range(beginId, endId + 1):
        print("id_state = ", str(id_state))
        print("id_phase = ", str(id_phase))
        phase_fixed = cs_result.contactPhases[id_phase]  # phase where the CoM move and the contacts are fixed
        # set initial state to be the final one of the previous phase :
        if id_phase > 1:
            setInitialFromFinalValues(phase_swing, phase_fixed)
        # compute 'optimal' position of the COM to go before switching phase:
        if id_state == endId:
            c = phase_fixed.c_final
        else:
            c = getTargetCOMPosition(fullBody, id_state, cfg.COM_SHIFT_Z)
            # set 'c' the final position of current phase :
            phase_fixed.c_final = c
        phase_fixed.dc_final = np.zeros(3)
        phase_fixed.ddc_final = np.zeros(3)
        connectPhaseTrajToFinalState(phase_fixed)

        id_phase += 1
        if id_state < endId:
            phase_swing = cs_result.contactPhases[id_phase]  # phase where the CoM is fixed and an effector move
            # in swing phase, com do not move :
            setInitialFromFinalValues(phase_fixed,phase_swing)
            copyPhaseInitToFinal(phase_swing)
            connectPhaseTrajToFinalState(phase_swing)
            id_phase += 1

    return cs_result
